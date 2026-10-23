#!/usr/bin/env python3
"""Fail when any part of the deleted 1.16.2 -> 1.17 -> 2.7.2.0 carry API is still used in code.

What this gate was, and what it is now
======================================
Until 2026-10-23 this gate was a ratchet over `transmute(base + SOME_RVA)` and its kin: every game
address in the workspace was a 1.16.2 constant, `er_game_base::game_build` translated it to the
running build through generated ledger tables, and a raw `base + RVA` that skipped the translation
read the 1.16.2 slot of a 1.17 image. That premise is gone. Every address is now a native 2.7.2.0
rva, the gate translates nothing, and `game_base + rva` is the running game's address on the one
build the workspace supports.

What can still go wrong is the inverse: a piece of the deleted carry surviving somewhere it is
compiled. Each of these was public, or reached through a generated file, and each one means a
constant is being treated as some other build's address:

  * the carry functions -- `game_rva_measured_on`, `carry_1171_rva`, `carry_1171_to_1172`,
    `carry_mapped_rva`, `table_answer`, `already_translated_in`, `resolve_on_running_build*` --
    and their vocabulary: `MAPPED_FILE_VERSION*`, `CARRY_1171_*`, `Carry::{Identity,To1171,To1172}`,
    `TableAnswer`, the generated `VERIFIED_1162_TO_1170` / `DETOUR_SAFE_1162_TO_1170` /
    `CARRY_1171_TO_1172` tables and `address_map_1170.rs`, and the translation statistics
    (`address_translation_stats`, `AddressTranslationStats`, `verified_translation_count`);
  * the prologue images of the deleted builds, `Image::EldenRing1170` / `Image::EldenRing1171`,
    and their `ER_DEOBF_BIN_1170` / `ER_DEOBF_BIN_1171` overrides;
  * the ledgers themselves, named by path: `docs/recon/rva-map-1162-to-1170*`,
    `carry-1171-to-1172.tsv`, `rva-1170-quarantine.tsv`, `rva-1170-observed-refusals.txt`;
  * the suffixless hub exports `er_effects_union_register` / `er_effects_union_register5`. They
    were renamed to `*_2720` because the address a companion hands the hub changed meaning
    without changing type: a companion that still looks up the old name finds no hub, and one
    built against an old hub would hand it addresses of the wrong build.

The scan reads code and string literals and skips comments, because prose about the history is
how a reader learns why the names are gone; a name in a comment is never compiled. It covers
`crates/`, `build-support/` and `tools/`, so a build script that still reads a ledger is caught
with the crate that includes its output. The target is zero, so there is no baseline: a finding is
a file to fix, not a number to ratchet.

    python3 scripts/check-stale-rva-calls.py             # enforce
    python3 scripts/check-stale-rva-calls.py --selftest  # prove every pattern fires, and only in code
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_ROOTS = ("crates", "build-support", "tools")
SCAN_SUFFIXES = (".rs", ".toml")

# `(label, pattern)`. Word boundaries on both sides, so a native successor that merely starts with
# a deleted name (`TEXT_RVA_END_2720`, `er_effects_union_register_2720`) is not a finding.
DELETED = (
    (
        "carry function",
        re.compile(
            r"\b(?:game_rva_measured_on|carry_1171_rva|carry_1171_to_1172|carry_mapped_rva"
            r"|carry_shift_for_running_build|carry_shift_or_refuse|table_answer"
            r"|already_translated_in|resolve_on_running_build(?:_quiet)?)\b"
        ),
    ),
    (
        "carry vocabulary",
        re.compile(
            r"\b(?:MAPPED_FILE_VERSION(?:_\w+)?|CARRY_1171_\w+|TableAnswer"
            r"|Carry\s*::\s*(?:Identity|To1171|To1172))\b"
        ),
    ),
    (
        "generated translation table",
        re.compile(r"\b(?:VERIFIED_1162_TO_1170|DETOUR_SAFE_1162_TO_1170|CARRY_1171_TO_1172)\b|address_map_1170"),
    ),
    (
        "translation statistics",
        re.compile(r"\b(?:address_translation_stats|AddressTranslationStats|verified_translation_count)\b"),
    ),
    ("deleted prologue image", re.compile(r"\b(?:EldenRing1170|EldenRing1171|ER_DEOBF_BIN_117[01])\b")),
    (
        "deleted ledger path",
        re.compile(r"rva-map-1162-to-1170|carry-1171-to-1172|rva-1170-quarantine|rva-1170-observed-refusals"),
    ),
    ("pre-2.7.2.0 hub export name", re.compile(r"\ber_effects_union_register5?\b")),
)

# `r"..."` / `r#"..."#` openers, and a char literal -- tracked so a quote or a slash inside one
# cannot desynchronise the comment scan. A lifetime (`'a`) deliberately does not match.
RAW_STRING = re.compile(r"r(#*)\"")
CHAR_LITERAL = re.compile(r"'(?:\\.|[^\\'])'")


def strip_comments(text):
    """`text` with Rust comments blanked to spaces, string literals kept, newlines preserved.

    Strings are kept on purpose: two of the deleted names live in them -- a ledger path handed to
    `include_str!` or `fs::read`, and a hub export looked up with `GetProcAddress(.., b"name\\0")`.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
        elif ch == "r" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")) and (
            m := RAW_STRING.match(text, i)
        ):
            close = '"' + "#" * len(m.group(1))
            end = text.find(close, m.end())
            end = n if end == -1 else end + len(close)
            out.append(text[i:end])
            i = end
        elif ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif ch == "'" and (m := CHAR_LITERAL.match(text, i)):
            out.append(m.group(0))
            i = m.end()
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def strip_toml_comments(text):
    """TOML `#` comments blanked, outside of quoted strings."""
    lines = []
    for line in text.split("\n"):
        quoted, cut = None, len(line)
        for index, ch in enumerate(line):
            if quoted:
                if ch == quoted:
                    quoted = None
            elif ch in "\"'":
                quoted = ch
            elif ch == "#":
                cut = index
                break
        lines.append(line[:cut] + " " * (len(line) - cut))
    return "\n".join(lines)


def findings_in(text, relative):
    """`[(relative:line, label, matched text, source line)]` for one file."""
    code = strip_toml_comments(text) if relative.endswith(".toml") else strip_comments(text)
    lines = text.split("\n")
    out = []
    for label, pattern in DELETED:
        for match in pattern.finditer(code):
            line = code.count("\n", 0, match.start()) + 1
            out.append((f"{relative}:{line}", label, match.group(0), lines[line - 1].strip()[:140]))
    return sorted(out)


def sources(root=ROOT):
    for top in SCAN_ROOTS:
        base = os.path.join(root, top)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ("target", ".git")]
            for name in sorted(filenames):
                if name.endswith(SCAN_SUFFIXES):
                    yield os.path.join(dirpath, name)


def scan(root=ROOT):
    found, files = [], 0
    for path in sources(root):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except FileNotFoundError:
            continue  # vanished between the listing and the read; not source this run can judge
        files += 1
        found.extend(findings_in(text, os.path.relpath(path, root)))
    return found, files


# Each planted line is a real pre-port shape, frozen from the tree as it stood before 2026-10-23,
# so every pattern above is proved against something the carry actually wrote.
PLANTED = (
    ("carry function", "let Ok(a) = er_game_base::mem::game_rva_measured_on(CAN_USE_GOODS, MAPPED_FILE_VERSION_1171) else { return };"),
    ("carry function", "let Some(rva) = er_game_base::game_build::carry_1171_rva(SITE_RVA) else { return None };"),
    ("carry vocabulary", "if er_game_base::game_build::game_file_version() == Some(MAPPED_FILE_VERSION_1172) {"),
    ("carry vocabulary", "match carry { Carry::To1172 => {} _ => {} }"),
    ("generated translation table", 'include!(concat!(env!("OUT_DIR"), "/address_map_1170.rs"));'),
    ("generated translation table", "let rows = VERIFIED_1162_TO_1170.len();"),
    ("translation statistics", "let stats = er_game_base::game_build::address_translation_stats();"),
    ("deleted prologue image", "image: Image::EldenRing1171,"),
    ("deleted prologue image", 'println!("cargo:rerun-if-env-changed=ER_DEOBF_BIN_1170");'),
    ("deleted ledger path", 'let tsv = root.join("../../docs/recon/carry-1171-to-1172.tsv");'),
    ("pre-2.7.2.0 hub export name", 'let f = GetProcAddress(hub, b"er_effects_union_register\\0".as_ptr());'),
    ("pre-2.7.2.0 hub export name", "pub unsafe extern \"C\" fn er_effects_union_register5(target: usize) -> usize {"),
)

# The same names where they are allowed: in prose, and as the prefix of a native successor.
ALLOWED = (
    "// carry_1171_rva and MAPPED_FILE_VERSION_1171 were deleted on 2026-10-23",
    "/// Replaces `game_rva_measured_on(.., MAPPED_FILE_VERSION_1172)`; see docs/recon/rva-map-1162-to-1170.tsv.",
    "/* er_effects_union_register was renamed */ let f = er_effects_union_register_2720;",
    'let f = GetProcAddress(hub, b"er_effects_union_register5_2720\\0".as_ptr());',
    "const TEXT_RVA_END_2720: u32 = 0x29a_7000;",
    "let s = r#\"// not a comment\"#; let quote = '\"'; // carry_1171_rva",
)


def selftest():
    global DELETED  # noqa: PLW0603 - swapped once below to prove the scan is not vacuous
    failures = []

    for label, line in PLANTED:
        got = {hit[1] for hit in findings_in(line + "\n", "fixture.rs")}
        if label not in got:
            failures.append(f"planted {label!r} was not found in: {line}")
    for line in ALLOWED:
        got = findings_in(line + "\n", "fixture.rs")
        if got:
            failures.append(f"an allowed line was reported ({[h[2] for h in got]}): {line}")
    toml = '[features]\nlegacy = []  # was gated on MAPPED_FILE_VERSION_1171\npath = "carry-1171-to-1172.tsv"\n'
    got = [hit[2] for hit in findings_in(toml, "Cargo.toml")]
    if got != ["carry-1171-to-1172"]:
        failures.append(f"a TOML file read the comment or missed the string: {got}")

    # Whole-file: every pattern fires in one planted tree, and a tree of allowed lines is clean.
    scratch = tempfile.mkdtemp()
    os.makedirs(os.path.join(scratch, "crates", "planted", "src"))
    with open(os.path.join(scratch, "crates", "planted", "src", "lib.rs"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(line for _label, line in PLANTED) + "\n")
    os.makedirs(os.path.join(scratch, "build-support"))
    with open(os.path.join(scratch, "build-support", "ok.rs"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(ALLOWED) + "\n")
    planted, files = scan(scratch)
    if files != 2:
        failures.append(f"the planted tree should read 2 files, read {files}")
    if {hit[1] for hit in planted} != {label for label, _pattern in DELETED}:
        missing = {label for label, _pattern in DELETED} - {hit[1] for hit in planted}
        failures.append(f"the planted tree did not exercise every pattern; missing {sorted(missing)}")
    if any(hit[0].startswith("build-support") for hit in planted):
        failures.append("the allowed lines under build-support/ produced findings")

    # Non-vacuity: blinding the scanner must lose the planted findings.
    keep = DELETED
    try:
        DELETED = tuple((label, re.compile(r"\bthis_matches_nothing_at_all\b")) for label, _p in keep)
        if scan(scratch)[0]:
            failures.append("the scanner still reported findings with every pattern blinded")
    finally:
        DELETED = keep

    # The real tree must be read, and must be clean.
    real, files = scan()
    if files < 500:
        failures.append(f"only {files} source file(s) read from {', '.join(SCAN_ROOTS)}; the walk is broken")
    if real:
        failures.append(f"the real tree has {len(real)} finding(s); run the gate for the list")

    for line in failures:
        print(f"check-stale-rva-calls selftest FAILED -- {line}", file=sys.stderr)
    if failures:
        return 1
    print(
        f"check-stale-rva-calls selftest: OK ({len(PLANTED)} planted shapes found, {len(ALLOWED)} "
        f"allowed lines clean, {files} real files, 0 findings)"
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    found, files = scan()
    if not found:
        print(f"check-stale-rva-calls: OK -- {files} file(s) under {', '.join(SCAN_ROOTS)}, no deleted carry API in code")
        return 0
    for where, label, matched, source in found:
        print(f"  {where}: {label} `{matched}`\n      {source}")
    print(
        f"\ncheck-stale-rva-calls: {len(found)} use(s) of the deleted carry API. Every game address "
        "is a native 2.7.2.0 rva now: use it as written (er_game_base::mem::game_rva / "
        "game_data_addr add the build check and nothing else), name the hub as "
        "er_effects_union_register_2720 / er_effects_union_register5_2720, and pin prologues with "
        "Image::EldenRing2720. docs/game-build.md has the model."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
