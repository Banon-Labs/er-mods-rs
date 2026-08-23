#!/usr/bin/env python3
"""Fail when two ME3-loadable DLLs detour the same address without a conflict-table entry.

WHY THIS GATE EXISTS
--------------------
Two MinHook instances on one prologue overwrite each other's trampolines. The DLL that loses
does not crash, does not log an error, and reports its hook as installed -- it simply never
runs. Every feature behind that detour then looks unimplemented.

That is not hypothetical. Measured 2026-08-23: `er-effects-rs` and `er-armament-icons` both
detour the Scaleform file-open wrapper at `TITLE_SCALEFORM_FILE_OPEN_RVA` (0x11ced80). In an
eleven-native profile the product DLL reported `file_open_observer_installed = true` and
`file_open_hits = 0` for an entire session; the System>Quit tab rendered vanilla, the title
strip, stats panel and both text-input movies were all silently inert. Loaded alone, the same
build reported 113 hits and derived its movie correctly. The whole day's symptom was a loader
bug wearing a feature bug's clothes.

WHY GREPPING FOR ADDRESSES WOULD NOT HAVE CAUGHT IT
--------------------------------------------------
Both sides referenced the same NAMED constant (`er_game_base::rva::TITLE_SCALEFORM_FILE_OPEN_RVA`)
rather than a literal, so searching the tree for `0x11ced80` finds only its single definition.
This gate therefore matches on the NAME as well as on literal addresses.

WHAT IT DOES
------------
For every cdylib crate (the ME3-loadable shells), collect the hook targets it names: shared RVA
constants from `er_game_base::rva`, and bare `0x1xxxxxxx`-shaped literals on lines that look like
hook installation. Any target claimed by two or more cdylibs must appear in `scripts/me3-dll-conflicts.toml`, either
as a `[[conflict]]` pair (they must never share a profile) or as a `[[shared]]` pair (they may,
because both route through ONE MinHook instance via the hook union), or this fails.

Being listed as a conflict is not a fix -- it is a decision, recorded where the profile generator
can act on it. `[[shared]]` IS the fix, and this gate proves it rather than taking its word: each
side names its detour (`handler_a` / `handler_b`), and that symbol must be handed to a union
registrar and must never appear in an `MhHook::new(...)` call. Reverting either side to a private
MinHook instance is then a red gate, not another silent session.

That is how the measured pair above was closed on 2026-08-23: the product's observer moved to
`er_hook::register_union_hook`, and `er-armament-icons` moved to `er_hook::register_shared_hook`,
which resolves the product's `er_effects_union_register` export and chains into its instance
(falling back to its own union when the product is absent). Install order no longer decides it.

Usage:
    python3 scripts/check-shared-hook-rvas.py
    python3 scripts/check-shared-hook-rvas.py --selftest
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATES = REPO_ROOT / "crates"
CONFLICTS = REPO_ROOT / "scripts" / "me3-dll-conflicts.toml"

# A shared RVA constant, however it is spelled at the use site: `er_game_base::rva::FOO`,
# `rva::FOO`, or a bare `FOO` that was imported. Only SCREAMING_SNAKE names ending in a
# hook-ish suffix count, so ordinary constants are not swept up.
RVA_NAME = re.compile(r"\b([A-Z][A-Z0-9_]*_(?:RVA|ADDRESS|PROLOGUE))\b")

# A literal that looks like a game code address. The image base is 0x140000000 and RVAs are
# written both ways in this tree, so accept either shape.
RVA_LITERAL = re.compile(r"\b0x1[0-9a-fA-F]{6,8}\b")

# Lines that plausibly INSTALL a hook rather than merely mention an address. Without this a
# comment quoting an address would be read as a claim on it.
INSTALLS = re.compile(
    r"mh_install|MH_CreateHook|create_hook|install_hook|detour|hook_once|\bhooked\b", re.I
)

# A crate also claims an address by ALIASING it into its own hook-target constant, which is how
# `er-armament-icons` claims the Scaleform file-open wrapper:
#     const FILE_OPEN_RVA: usize = er_game_base::rva::TITLE_SCALEFORM_FILE_OPEN_RVA;
# There is no `mh_install` on that line, so an install-only rule misses the one pair this gate
# was written for -- a false green of exactly the kind that already cost this project a day.
ALIAS = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+[A-Z][A-Z0-9_]*\s*:")

# Names that are READ, never detoured: singleton pointers, vtables, data blobs. Any number of
# DLLs may read the same global; only a shared PROLOGUE corrupts anything. Without this the gate
# reported 14 collisions of which 13 were harmless, and a gate that cries wolf gets muted.
READ_ONLY = re.compile(r"GLOBAL|SINGLETON|VTABLE|_DATA_|REPOSITORY")


def cdylib_crates() -> dict[str, Path]:
    """Every crate that builds an ME3-loadable DLL, by package name."""
    found: dict[str, Path] = {}
    for manifest in sorted(CRATES.glob("*/Cargo.toml")):
        text = manifest.read_text(encoding="utf-8")
        crate_type = re.search(r"crate-type\s*=\s*\[([^\]]*)\]", text)
        if not crate_type or "cdylib" not in crate_type.group(1):
            continue
        name = re.search(r'name\s*=\s*"([^"]+)"', text)
        if name:
            found[name.group(1)] = manifest.parent
    return found


def hook_targets(crate_dir: Path) -> dict[str, str]:
    """Hook targets this crate names, mapped to the first file:line that names them."""
    targets: dict[str, str] = {}
    for source in sorted(crate_dir.rglob("*.rs")):
        if "target" in source.parts:
            continue
        try:
            lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            where = f"{source.relative_to(REPO_ROOT)}:{number}"
            # A CLAIM IS AN INSTALL, NOT A MENTION.
            #
            # The first version counted every `*_RVA` name anywhere in a crate, and reported 14
            # collisions of which 13 were harmless: `GAME_DATA_MAN_GLOBAL_RVA`,
            # `CS_MENU_MAN_GLOBAL_RVA`, `SCALEFORM_MEMORY_FILE_VTABLE_RVA` and friends are
            # singleton pointers and vtables that DLLs merely READ. Any number of DLLs can read
            # the same global; only detouring the same prologue corrupts anything. A gate that
            # cries wolf 13 times out of 14 gets muted, which would cost more than it saves.
            if not (INSTALLS.search(line) or ALIAS.match(line)):
                continue
            for name in RVA_NAME.findall(line):
                if not READ_ONLY.search(name):
                    targets.setdefault(name, where)
            for literal in RVA_LITERAL.findall(line):
                targets.setdefault(literal.lower(), where)
    return targets


def relative(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise -- so a fixture outside the tree (the
    selftest's synthetic crate) reports a path instead of raising."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_table() -> dict:
    if not CONFLICTS.exists():
        return {}
    return tomllib.loads(CONFLICTS.read_text(encoding="utf-8"))


def declared_pairs() -> set[frozenset[str]]:
    """Pairs whose sharing of an address is a recorded decision, either way.

    `[[conflict]]` = they must never share a profile. `[[shared]]` = they may, because one MinHook
    instance owns the prologue and both handlers chain. Both are answers; only silence is not.
    """
    table = load_table()
    rows = [*table.get("conflict", []), *table.get("shared", [])]
    return {frozenset({row["a"], row["b"]}) for row in rows}


# A line that hands a detour to a union registrar -- the product's own union, the cross-DLL helper,
# or the product's C export resolved from a companion image.
UNION_REGISTER = re.compile(r"register_union_hook|register_shared_hook|er_effects_union_register")
# A line that creates a PRIVATE MinHook detour. On a `[[shared]]` handler this is the regression the
# whole entry exists to prevent, so its presence is a failure rather than a warning.
BARE_HOOK = re.compile(r"MhHook::new|MH_CreateHook")


# How far from a handler mention to look for the call that installs it. rustfmt splits a
# three-argument register call across four lines, so a same-line rule reports "never reaches a union
# registrar" for code that plainly does -- a false RED, which erodes a gate as fast as a false green.
HANDLER_CALL_WINDOW = 5


def handler_sites(crate_dir: Path, handler: str) -> tuple[list[str], list[str]]:
    """Where `handler` is union-registered, and where it is bare-hooked, as file:line lists."""
    unioned: list[str] = []
    bare: list[str] = []
    symbol = re.compile(rf"\b{re.escape(handler)}\b")
    for source in sorted(crate_dir.rglob("*.rs")):
        if "target" in source.parts:
            continue
        try:
            lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*") or not symbol.search(line):
                continue
            low = max(0, index - HANDLER_CALL_WINDOW)
            high = min(len(lines), index + HANDLER_CALL_WINDOW + 1)
            # Comments are excluded from the window too: this file's own prose names both the
            # registrars and `MhHook::new` while explaining them, and reading documentation as an
            # install site would make the check assert whatever the comment last mentioned.
            window = "\n".join(
                l for l in lines[low:high] if not l.strip().startswith(("//", "*"))
            )
            where = f"{relative(source)}:{index + 1}"
            if UNION_REGISTER.search(window):
                unioned.append(where)
            if BARE_HOOK.search(window):
                bare.append(where)
    return unioned, bare


def shared_mechanism_failures(crates: dict[str, Path]) -> list[str]:
    """Prove every `[[shared]]` row's claim that both detours go through ONE MinHook instance.

    A `[[shared]]` row is the ONLY thing that lets two DLLs co-load on one prologue, so it may not
    be a promise. Each side names its detour symbol; that symbol must reach a union registrar and
    must never reach `MhHook::new`. Without this the table could keep asserting the pair is safe
    long after someone reinstated a private instance -- which is the exact failure mode (silent,
    uncrashing, feature-shaped) that the union was added to end.
    """
    failures: list[str] = []
    for index, row in enumerate(load_table().get("shared", []), 1):
        where = f"[[shared]] #{index}"
        for side in ("a", "b"):
            package = row.get(side)
            handler = row.get(f"handler_{side}")
            if not package or not handler:
                failures.append(f"{where}: missing '{side}' or 'handler_{side}'")
                continue
            crate_dir = crates.get(package)
            if crate_dir is None:
                failures.append(f"{where}: '{package}' is not a cdylib crate in this workspace")
                continue
            unioned, bare = handler_sites(crate_dir, handler)
            if bare:
                failures.append(
                    f"{where}: {package}'s handler '{handler}' is installed with a PRIVATE MinHook "
                    f"at {', '.join(bare)} -- a [[shared]] pair must route through the union, or "
                    "the two instances overwrite each other's trampolines and the loser goes silent"
                )
            if not unioned:
                failures.append(
                    f"{where}: {package}'s handler '{handler}' never reaches a union registrar "
                    "(register_union_hook / register_shared_hook / er_effects_union_register)"
                )
    return failures


def selftest() -> int:
    failures = 0

    def case(name: str, condition: bool) -> None:
        nonlocal failures
        if not condition:
            print(f"selftest FAIL: {name}", file=sys.stderr)
            failures += 1

    case("named rva constant", RVA_NAME.findall("const X: usize = rva::TITLE_FOO_RVA;") == ["TITLE_FOO_RVA"])
    case("address suffix too", RVA_NAME.findall("SCAN_START_ADDRESS") == ["SCAN_START_ADDRESS"])
    case("ordinary const ignored", RVA_NAME.findall("const MAX_ROWS: usize = 6;") == [])
    case("literal shape", RVA_LITERAL.findall("mh_install(0x1411ced80)") == ["0x1411ced80"])
    case("short hex ignored", RVA_LITERAL.findall("let mask = 0x1f;") == [])
    case("install line detected", INSTALLS.search("mh_install_hook_once(&FLAG, 0, 1, addr)") is not None)
    case("plain mention is not an install", INSTALLS.search("let base = 0x1411ced80;") is None)
    # Reading a singleton is not claiming it: 13 of the first run's 14 reports were this.
    case(
        "a read of a global is not a claim",
        INSTALLS.search("let gdm = read(base + GAME_DATA_MAN_GLOBAL_RVA);") is None,
    )
    case("an alias line is a claim", ALIAS.match("const FILE_OPEN_RVA: usize = rva::TITLE_FOO_RVA;") is not None)
    case("a plain let is not a claim", ALIAS.match("    let x = TITLE_FOO_RVA;") is None)
    case("globals are reads", READ_ONLY.search("GAME_DATA_MAN_GLOBAL_RVA") is not None)
    case("a code prologue is not", READ_ONLY.search("TITLE_SCALEFORM_FILE_OPEN_RVA") is None)

    case("a union register line is recognised", UNION_REGISTER.search("register_shared_hook(a, f, &O)") is not None)
    case("a bare hook line is recognised", BARE_HOOK.search("MhHook::new(addr, detour)") is not None)
    case("a union line is not a bare hook", BARE_HOOK.search("register_union_hook(a, f, &O)") is None)

    # END-TO-END NEGATIVE CONTROL. The regex cases above prove the patterns; these prove the SCAN
    # built on them still separates the two shapes across the multi-line calls rustfmt actually
    # produces. Without this the mechanism check could quietly stop finding anything and report
    # every [[shared]] row as verified -- the same false green this file already shipped once.
    with tempfile.TemporaryDirectory() as raw:
        fixture = Path(raw)
        (fixture / "src").mkdir()
        source = fixture / "src" / "fixture.rs"
        source.write_text(
            "fn go() {\n    let h = MhHook::new(\n        addr,\n"
            "        my_detour as *mut c_void,\n    );\n}\n",
            encoding="utf-8",
        )
        unioned, bare = handler_sites(fixture, "my_detour")
        case("a multi-line bare hook is caught", bare and not unioned)
        source.write_text(
            "fn go() {\n    register_union_hook(\n        addr,\n"
            "        my_detour,\n        &ORIG,\n    );\n}\n",
            encoding="utf-8",
        )
        unioned, bare = handler_sites(fixture, "my_detour")
        case("a multi-line union registration passes", unioned and not bare)

    # THE REGRESSION THIS GATE IS NAMED FOR -- and a NEGATIVE CONTROL for the gate itself.
    # Asserting only that the table lists the pair would pass even if the scan stopped finding
    # it, which is precisely how the first narrowing shipped green while detecting nothing.
    measured = frozenset({"er-effects-rs", "er-armament-icons"})
    case("the measured pair is declared", measured in declared_pairs())
    crates = cdylib_crates()
    claims: dict[str, set[str]] = {}
    for crate_name, directory in crates.items():
        for target in hook_targets(directory):
            claims.setdefault(target, set()).add(crate_name)
    case(
        "the scan still SEES the measured pair",
        {"er-effects-rs", "er-armament-icons"}
        <= claims.get("TITLE_SCALEFORM_FILE_OPEN_RVA", set()),
    )
    # The pair is declared SHARED (co-loadable), not merely declared -- and the mechanism holds.
    shared_pairs = {
        frozenset({row["a"], row["b"]}) for row in load_table().get("shared", [])
    }
    case("the measured pair is declared SHARED, not conflicting", measured in shared_pairs)
    case("every [[shared]] row's union mechanism verifies", shared_mechanism_failures(crates) == [])

    if failures:
        print(f"selftest: {failures} case(s) failed", file=sys.stderr)
        return 1
    print("[check-shared-hook-rvas] selftest ok (21 cases)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    crates = cdylib_crates()
    claims: dict[str, dict[str, str]] = {}
    for name, directory in crates.items():
        for target, where in hook_targets(directory).items():
            claims.setdefault(target, {})[name] = where

    mechanism_failures = shared_mechanism_failures(crates)
    if mechanism_failures:
        print(
            f"[check-shared-hook-rvas] FAIL: {len(mechanism_failures)} [[shared]] declaration(s) in "
            "scripts/me3-dll-conflicts.toml are not backed by the union mechanism they claim. A "
            "[[shared]] row is what allows two DLLs to co-load on one prologue; if either side "
            "installs a private MinHook instead, the two overwrite each other's trampolines and the "
            "loser reports installed, never runs, and its feature looks unimplemented.",
            file=sys.stderr,
        )
        for failure in mechanism_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    declared = declared_pairs()
    undeclared: list[tuple[str, list[str], dict[str, str]]] = []
    for target, owners in sorted(claims.items()):
        if len(owners) < 2:
            continue
        names = sorted(owners)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if frozenset({a, b}) not in declared:
                    undeclared.append((target, [a, b], owners))

    if undeclared:
        print(
            f"[check-shared-hook-rvas] FAIL: {len(undeclared)} hook target(s) claimed by two ME3 "
            "DLLs with no conflict-table entry. Two MinHook instances on one prologue overwrite "
            "each other's trampolines: the loser reports installed, never runs, and its features "
            "look unimplemented. Either add a [[conflict]] pair to scripts/me3-dll-conflicts.toml, "
            "or make one side stop hooking and subscribe to the other's detour.",
            file=sys.stderr,
        )
        for target, (a, b), owners in undeclared:
            print(f"  - {target}: {a} ({owners[a]})  vs  {b} ({owners[b]})", file=sys.stderr)
        return 1

    shared = sum(1 for owners in claims.values() if len(owners) > 1)
    print(
        f"[check-shared-hook-rvas] ok -- {len(crates)} ME3 DLL(s), {len(claims)} hook target(s), "
        f"{shared} shared and all declared"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
