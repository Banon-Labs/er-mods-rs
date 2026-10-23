#!/usr/bin/env python3
"""Prove the prologue sweep sees every `PrologueSpec`, and measure how much each pin discriminates.

Why this exists
---------------
A migration doc once claimed "all 36 specs swept against the 1.17 image".  There was no set of 36
specs: the generator emits two constants per spec (a pin and a `_MASK`), so the numbers in
circulation -- 36, 84, "48 never swept" -- came from comparing a constant count against a spec
count.  Every spec was in fact already swept, because `verify-aob-patterns.py` globs
`crates/*/build.rs`, not one crate.  The defect was never an unswept spec -- it was that a document
asserted a coverage number that nothing computed.  A count in prose cannot go red.

So this gate computes the number instead of asserting it, and fails when the sweep's enumerator
cannot reach a spec that exists.  Two sections:

  coverage    Walk the whole repository for `PrologueSpec` declarations and compare that set
              against what `verify-aob-patterns.py` can actually enumerate.  A spec-bearing file
              the sweep cannot see is a hard failure.  `_build_scripts()` globs
              `crates/*/build.rs`, which is exactly one directory level deep, so a crate whose
              build script moves to `crates/foo/dll/build.rs` would become invisible to the sweep
              while still shipping live prologue gates.  It would not appear as a skip or a gap; it
              would not appear at all, and the sweep would keep printing a green total that
              silently shrank.

  uniqueness  For each spec, count how many places in `eldenring-deobf-1.17.2.bin` the pin matches
              under its own mask, and say whether the pin's own VA is one of them.

What `uniqueness` does and does not claim
-----------------------------------------
Read this before quoting a number out of it.

A prologue gate is not a scanner.  The DLL takes its address from a constant and compares bytes
at that address; it never searches.  So a pin that also matches elsewhere does not mean the hook
lands in the wrong place -- the address did not come from the pattern.

What multiplicity measures is the gate's discriminating power: whether the byte check could tell
that a wrong address is wrong.  A 7-byte MSVC opening like `40 53 57 48 83 ec 68` occurs in
thousands of functions, so it will accept essentially any function entry handed to it.  That is
not a bug in the pin -- it is what the function's first bytes are -- but it does bound what an
arms verdict is worth.  bd
`stale-aob-that-still-matches-is-worse-than-no-match-calibrate-on-the-old-image-2026-08-30` is
about precisely this failure shape: agreement that is confident and uninformative.

Verdicts:
  Unique      one match in the whole image, at the pin's VA.  Arms is decisive here.
  Ambiguous   matches at the pin's VA and elsewhere.  Arms is correct but weak: the pin would
              have accepted N other addresses just as happily.  Not a defect by itself.
  Misplaced   matches somewhere, but not at the pin's VA.  The bytes are right and the address
              is wrong.
  Absent      matches nowhere in the image.

Usage
    python3 scripts/verify-prologue-coverage.py                 # both sections
    python3 scripts/verify-prologue-coverage.py --selftest      # no image, no build needed
    python3 scripts/verify-prologue-coverage.py --section coverage
    python3 scripts/verify-prologue-coverage.py --require-images
    python3 scripts/verify-prologue-coverage.py --json <path>   # machine-readable dump

Exit 0 = the sweep can enumerate every declared spec.  Exit non-zero = a count of problems.

`uniqueness` is reporting, not a gate: it never contributes to the exit code, because a low-power
pin is a fact about the game's code and not something a commit can be blocked on.  Only
`coverage` fails the build; `verify-aob-patterns.py --section prologues` is what fails a pin that
does not match its own address.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import er_game_image  # noqa: E402 - repo-local, and the sys.path line above is what makes it work
from repo_source_scan import NOT_REPO_SOURCE, REPO_ROOT as ROOT, iter_rust_sources  # noqa: E402

BASE = er_game_image.BASE
COMPARED = 0xFF
IGNORED = 0x00

# The shared "not this repo's source" set (.git/.worktrees/.claude/target/third_party), plus two
# this gate alone excludes: a vendored or npm-installed tree can carry a `PrologueSpec`-shaped
# literal that this repo never declared, and a phantom spec makes the coverage comparison lie.
EXTRA_SKIP_DIRS = frozenset({"node_modules", "vendor"})
SKIP_DIRS = NOT_REPO_SOURCE | EXTRA_SKIP_DIRS


def _load(name: str, filename: str):
    """Import a sibling script by path. These are `-`-named files, so a plain import cannot."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The sweep whose coverage is being audited. Imported, never re-implemented: a copied enumeration
# rule is how the two halves would drift apart again, which is the whole defect this file exists
# to close.
AOB = _load("verify_aob_patterns", "verify-aob-patterns.py")


# --- coverage ----------------------------------------------------------------------------------
def repo_spec_files() -> dict[Path, int]:
    """`{path: spec_count}` for every file in the repo that declares a `PrologueSpec`.

    Deliberately a full walk rather than a glob. The point of this section is to find a spec the
    sweep's glob cannot reach, so it must not share the sweep's glob.
    """
    found: dict[Path, int] = {}
    # `iter_rust_sources` is the shared full walk (scripts/repo_source_scan.py). It prunes the
    # non-source directories during the descent rather than reading and discarding them.
    for path in iter_rust_sources(ROOT, EXTRA_SKIP_DIRS):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        count = len(re.findall(r"PrologueSpec\s*\{", text))
        if count:
            found[path] = count
    return found


def swept_spec_files() -> set[Path]:
    """The files the sweep's own enumerator reaches."""
    return {Path(p).resolve() for p in AOB._build_scripts()}


def run_coverage() -> tuple[int, dict]:
    declared = repo_spec_files()
    # `build-support/prologue_build.rs` defines the struct; it declares no instances of it.
    declared = {p: n for p, n in declared.items() if p.name != "prologue_build.rs"}
    swept = swept_spec_files()
    enumerated = AOB.prologue_specs()
    gaps = AOB.prologue_spec_gaps()

    print("== coverage: every declared PrologueSpec is reachable by the sweep ==")
    print("   SEEN      the sweep's enumerator reaches this file.")
    print("   UNSWEPT   the file declares specs the sweep cannot reach -- it has no verdict.")
    problems = 0
    unswept = []
    for path in sorted(declared):
        rel = path.relative_to(ROOT)
        if path.resolve() in swept:
            print(f"    SEEN      {rel}  ({declared[path]} spec(s))")
        else:
            print(f"    UNSWEPT   {rel}  ({declared[path]} spec(s)) -- not enumerated by "
                  "verify-aob-patterns.py")
            unswept.append(str(rel))
            problems += declared[path]

    declared_total = sum(declared.values())
    print(f"\n  {len(declared)} file(s) declare {declared_total} spec(s); "
          f"the sweep enumerated {len(enumerated)}.")

    # A spec inside a visible file that the parser silently drops is the other half of the same
    # defect, and `prologue_spec_gaps()` already models it. Surfaced here so one command answers
    # "is anything unaccounted for", rather than two.
    for crate, name in gaps:
        print(f"    UNPARSED  {crate}/{name} -- declared, reachable, but the parser dropped it")
        problems += 1

    seen_total = sum(n for p, n in declared.items() if p.resolve() in swept)
    if seen_total != len(enumerated) + len(gaps):
        print(f"    MISCOUNT  reachable files declare {seen_total} spec(s) but the sweep "
              f"accounted for {len(enumerated) + len(gaps)}")
        problems += 1

    if not problems:
        print("  every declared spec is enumerated and accounted for.")
    return problems, {
        "declared_files": {str(p.relative_to(ROOT)): n for p, n in sorted(declared.items())},
        "declared_total": declared_total,
        "enumerated": len(enumerated),
        "unswept_files": unswept,
        "unparsed": [f"{c}/{n}" for c, n in gaps],
    }


# --- uniqueness --------------------------------------------------------------------------------
_GENERATED = re.compile(
    r"const (?P<name>[A-Z0-9_]+): (?:&\[u8\]|\[u8; \d+\]) = &?\[(?P<body>[^\]]*)\];", re.DOTALL
)
_HEXBYTE = re.compile(r"0x([0-9a-fA-F]{2})")


def generated_constants() -> dict[str, bytes]:
    """`{name: bytes}` for every generated prologue constant, newest build first.

    Same rule `verify-prologue-masks.py` uses: OUT_DIR accumulates one directory per
    build-script fingerprint and never prunes, so newest-first-wins is what describes the
    current build.
    """
    out: dict[str, bytes] = {}
    files = sorted(
        ROOT.glob("target/*/*/build/*/out/generated_*.rs"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _GENERATED.finditer(text):
            out.setdefault(
                match.group("name"), bytes(int(b, 16) for b in _HEXBYTE.findall(match.group("body")))
            )
    return out


def masked_regex(pin: bytes, mask: bytes) -> re.Pattern:
    """The pin as a byte regex: compared positions literal, ignored positions `.` under DOTALL."""
    parts = [
        re.escape(bytes([byte])) if keep == COMPARED else b"."
        for byte, keep in zip(pin, mask)
    ]
    return re.compile(b"".join(parts), re.DOTALL)


def classify_uniqueness(hits: list[int], target: int | None) -> str:
    if target is None:
        return "SKIP"
    if not hits:
        return "ABSENT"
    if target not in hits:
        return "MISPLACED"
    return "UNIQUE" if len(hits) == 1 else "AMBIGUOUS"


def run_uniqueness(require_images: bool, dump: dict) -> int:
    path = er_game_image.image_path()
    if path is None:
        print(f"\n== uniqueness: SKIPPED -- {er_game_image.missing_reason()}")
        return 1 if require_images else 0

    data = path.read_bytes()
    constants = generated_constants()

    print("\n== uniqueness: how many places each pin matches in the 2.7.2.0 image ==")
    print("   UNIQUE     one match, at the pin's VA -- ARMS is decisive.")
    print("   AMBIGUOUS  matches at the pin's VA and elsewhere -- ARMS is correct but weak.")
    print("   MISPLACED  matches, but not at the pin's VA.")
    print("   ABSENT     matches nowhere in the image.")
    print("   This section never fails the build.")

    rows = []
    tally: dict[str, int] = {}
    for crate, name, image_kind, va, _va_sym, pin in AOB.prologue_specs() + AOB.seam_specs():
        if AOB.IMAGE_ROLE.get(image_kind) != "game" or va is None:
            tally["SKIP"] = tally.get("SKIP", 0) + 1
            continue
        mask = constants.get(f"{name}_MASK") or bytes([COMPARED]) * len(pin)
        if len(mask) != len(pin):
            mask = bytes([COMPARED]) * len(pin)
        target = va - BASE
        pattern = masked_regex(pin, mask)
        hits = [m.start() for m in pattern.finditer(data)]
        verdict = classify_uniqueness(hits, target)
        tally[verdict] = tally.get(verdict, 0) + 1
        rows.append(
            {
                "crate": crate,
                "name": name,
                "image": image_kind,
                "va": f"{va:#x}",
                "verdict": verdict,
                "n": len(hits),
                "bytes": len(pin),
                "masked": sum(1 for k in mask if k == IGNORED),
                "others": [f"{BASE + h:#x}" for h in hits if h != target][:4],
            }
        )

    width = max((len(r["name"]) for r in rows), default=0)
    last = None
    for row in sorted(rows, key=lambda r: (r["crate"], r["name"])):
        if row["crate"] != last:
            print(f"\n  {row['crate']}")
            last = row["crate"]
        extra = ""
        if row["verdict"] == "MISPLACED" and row["others"]:
            extra = f"  found instead at {', '.join(row['others'])}"
        print(f"    {row['verdict']:<10s} {row['name']:<{width}s}  {row['bytes']:>2d}B  n={row['n']:<6d}{extra}")

    print("\n  totals: " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    weak = [r for r in rows if r["verdict"] == "AMBIGUOUS"]
    if weak:
        worst = max(weak, key=lambda r: r["n"])
        print(
            f"  {len(weak)} pin(s) match more than one site; the least discriminating is "
            f"{worst['name']} at {worst['n']} sites ({worst['bytes']} bytes)."
        )
    dump["uniqueness"] = {"rows": rows, "tally": tally}
    return 0


# --- selftest ----------------------------------------------------------------------------------
def selftest() -> int:
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # masked_regex honours the mask.
    pin = bytes([0x48, 0x8B, 0x05, 0xF1, 0x43, 0x6F, 0x03])
    mask = bytes([COMPARED, COMPARED, COMPARED, IGNORED, IGNORED, IGNORED, IGNORED])
    rx = masked_regex(pin, mask)
    check("mask ignores disp", bool(rx.match(bytes([0x48, 0x8B, 0x05, 0, 0, 0, 0]))), True)
    check("mask keeps opcode", bool(rx.match(bytes([0x49, 0x8B, 0x05, 0, 0, 0, 0]))), False)
    exact = masked_regex(pin, bytes([COMPARED]) * len(pin))
    check("unmasked is exact", bool(exact.match(bytes([0x48, 0x8B, 0x05, 0, 0, 0, 0]))), False)

    # A pin containing regex metacharacters is escaped, not interpreted. `0x2e` is `.`, `0x2a`
    # is `*`: an unescaped pin would match anything, and report every gate as ambiguous.
    meta = bytes([0x2E, 0x2A, 0x5B])
    mrx = masked_regex(meta, bytes([COMPARED]) * 3)
    check("metachars escaped", bool(mrx.match(bytes([0x41, 0x42, 0x43]))), False)
    check("metachars match self", bool(mrx.match(meta)), True)

    # classify_uniqueness covers every branch.
    check("unique", classify_uniqueness([0x10], 0x10), "UNIQUE")
    check("ambiguous", classify_uniqueness([0x10, 0x20], 0x10), "AMBIGUOUS")
    check("misplaced", classify_uniqueness([0x20], 0x10), "MISPLACED")
    check("absent", classify_uniqueness([], 0x10), "ABSENT")
    check("no address", classify_uniqueness([0x10], None), "SKIP")

    # The coverage walk must not read other agents' worktrees, and must find the real files.
    #
    # Asked relative to the repo root, not as a substring of the absolute path: every checkout
    # that lives under `.claude/worktrees/<agent>/` has `.claude` in the absolute path of each of
    # the repo's own legitimate files. A path genuinely inside a nested `.claude`/`.worktrees`/
    # `target` still trips it.
    declared = {p for p in repo_spec_files() if p.name != "prologue_build.rs"}
    leaked = [
        p
        for p in declared
        if set(p.relative_to(ROOT).parts[:-1]) & SKIP_DIRS
    ]
    check("no worktree leakage", bool(leaked), False)
    check("finds the five build scripts", len(declared) >= 5, True)

    # The sweep's enumerator must reach every file the walk found. This is the assertion the
    # whole file exists for; if it fails here it fails in `run_coverage` too.
    swept = swept_spec_files()
    check("every declared file is swept", sorted(p for p in declared if p.resolve() not in swept), [])

    for line in failures:
        print(f"  FAIL {line}")
    print(f"selftest: {'FAILED' if failures else 'ok'} ({len(failures)} failure(s))")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--section", choices=("all", "coverage", "uniqueness"), default="all")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--require-images", action="store_true")
    ap.add_argument("--json", type=Path, help="write the full result as JSON")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    bad = 0
    dump: dict = {}
    if args.section in ("all", "coverage"):
        problems, info = run_coverage()
        bad += problems
        dump["coverage"] = info
    if args.section in ("all", "uniqueness"):
        bad += run_uniqueness(args.require_images, dump)
    if args.json:
        args.json.write_text(json.dumps(dump, indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}")
    print(f"\nverdict: {'FAILED' if bad else 'ok'} ({bad} problem(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
