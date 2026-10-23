#!/usr/bin/env python3
"""Guard the game singletons the telemetry oracles read: one value in source, a pointer global in 2.7.2.0.

What went wrong once, and why it is the shape to guard
=====================================================
`er-telemetry-core`'s `standalone_tick` reads four game singletons to emit
`oracle_game_data_man_ptr`, `oracle_game_man_ptr`, `oracle_cs_menu_man_ptr`, `oracle_play_time_ms`
and the `oracle_flip_*` pair. On 2026-08-31 every one of them was wrong for two whole runs: the
addresses were 1.16.2 slots read on 1.17, and the reads did not fail -- they succeeded against
whatever occupied the old address. `oracle_game_data_man_ptr` reported `"0x6e614d6e6f697463"`,
little-endian ASCII `"ctionMan"`, eight bytes out of the middle of the RTTI type name
`.?AVNWSteamConnectionManager@DLNW3@@`. The other three landed in blank `.data` and read zero,
which is indistinguishable from a global the game has not created yet. A wrong pointer oracle
does not go quiet, it goes constant, and a constant is invisible.

The translation that made that possible is gone: since 2026-10-23 every address in the workspace
is a native 2.7.2.0 rva and `game_data_addr` adds the build check and nothing else. What is left
to get wrong is the constant itself, so this gate checks the constant, from both sides.

What it checks
==============
Source half (always runs). Each oracle singleton is declared, under its name, with one value: every
declaration of the name anywhere in `crates/` resolves (through `scripts/rva_symbols.py`) to the
same number. A second copy of the address that drifted from the first is how one reader of the
same global ends up on a different slot.

Image half (skips, saying so, when `eldenring-deobf-1.17.2.bin` is absent). The value must look
like a singleton pointer global in the 2.7.2.0 image, because a constant that merely parses is not
an address of anything:

  * it lies in a section the loader maps non-executable, and is 8-byte aligned;
  * its qword is zero at rest -- a pointer global is uninitialised in the static image;
  * it does not sit inside a printable-ASCII run, the RTTI/string-literal test the stale
    GameDataMan address fails;
  * at least `MIN_REFERENCES` `mov r64, [rip+disp]` instructions load it -- the game's own code
    treats it as a pointer it reads, which no amount of shape can stand in for.

Each of those is asserted to fail on a planted address in `--selftest`, so a detector that has
quietly stopped detecting cannot pass the gate.

    python3 scripts/check-oracle-singleton-globals.py             # enforce
    python3 scripts/check-oracle-singleton-globals.py --selftest  # prove the assertions can fail
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import function_extent  # noqa: E402  (path set above)
import rva_symbols  # noqa: E402

IMAGE_BASE = 0x140000000
IMAGE_ENV = "ER_DEOBF_BIN_1172"
IMAGE_NAME = "eldenring-deobf-1.17.2.bin"

# The singletons `standalone_tick` reads, by the name it reads them under. Values come from the
# tree, never from here: a second copy of the number is the drift the source half exists to catch.
ORACLE_SINGLETONS = (
    "GAME_DATA_MAN_GLOBAL_RVA",
    "GAME_MAN_SINGLETON_RVA",
    "CS_MENU_MAN_GLOBAL_RVA",
    "CS_FLIPPER_SINGLETON_RVA",
)
# The reader the oracles go through, so a rename of the tick is noticed rather than leaving this
# gate checking constants nothing reads.
TICK_SOURCE = REPO / "crates" / "er-telemetry-core" / "src" / "lib.rs"

# `mov <r64>, [rip+disp32]` -- REX.W (or REX.WR) + 8B + a modrm whose mod=00 rm=101. That is how
# every one of these globals is loaded, and restricting to it keeps the scan to one instruction
# length so no decoder is needed.
MOV_RIP_SCAN = re.compile(rb"[\x48\x4c]\x8b[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", re.S)
MOV_RIP_LENGTH = 7
DISPLACEMENT_AT = 3
# Independent loads required. The four live singletons have 113 to 786 each on 2.7.2.0; three is
# past the point where a coincidence explains a hit, and far below where an edit could starve it.
MIN_REFERENCES = 3
# A printable-ASCII run this long, straddling the address, means it is inside a string literal.
LITERAL_RUN = 8

# Planted addresses, frozen from `eldenring-deobf-1.17.2.bin` on 2026-10-23. Each must fail the
# image half, and for its own reason.
# `.?AVNWSteamConnectionManager@DLNW3@@` starts at 0x3d60f38; 0x10 in is the `ctionMan` the stale
# 1.17 read reported.
LITERAL_CONTROL_RVA = 0x3D60F48
LITERAL_CONTROL_TEXT = "ctionManager@DLNW3"
# An aligned, zero-at-rest `.data` qword that no `mov r64,[rip+disp]` loads.
UNREFERENCED_CONTROL_RVA = 0x3D64F00
# A function entry in `.text` (`FD4::FD4FileCap::AddDefaultFileLoadProcess`).
CODE_CONTROL_RVA = 0x265CC10


# --------------------------------------------------------------------------------------------
# Source half
# --------------------------------------------------------------------------------------------


def singleton_values(index, names=ORACLE_SINGLETONS):
    """`({name: value}, [problem])` -- one agreed value per name, or the reason there is none."""
    values, problems = {}, []
    for name in names:
        decls = index.by_simple.get(name, [])
        if not decls:
            problems.append(f"{name}: declared nowhere under crates/; the oracle reads a ghost")
            continue
        found = {}
        for decl in decls:
            for value in decl.value or ():
                found.setdefault(value, []).append(f"{os.path.relpath(decl.path, REPO)}:{decl.line}")
        unresolved = [d for d in decls if not d.value]
        if unresolved:
            problems.append(
                f"{name}: {len(unresolved)} declaration(s) do not evaluate to a number "
                f"({', '.join(f'{os.path.relpath(d.path, REPO)}:{d.line}' for d in unresolved[:3])})"
            )
        if len(found) != 1:
            shown = "; ".join(f"0x{v:x} at {', '.join(where[:3])}" for v, where in sorted(found.items()))
            problems.append(f"{name}: declarations disagree -- {shown or 'no values'}")
            continue
        values[name] = next(iter(found))
    return values, problems


def tick_problems(text):
    """The names the gate guards must be what `standalone_tick` reads, through `game_data_addr`."""
    code = rva_symbols.code_only(text)
    body = re.search(r"\bpub fn standalone_tick\s*\(\s*\)\s*\{", code)
    if body is None:
        return ["er-telemetry-core no longer defines `pub fn standalone_tick()`; re-read the oracles"]
    tail = code[body.end() :]
    problems = []
    if "game_data_addr(" not in tail[:6000]:
        problems.append("standalone_tick no longer reads its singletons through game_data_addr")
    for name in ORACLE_SINGLETONS:
        if not re.search(r"\b" + re.escape(name) + r"\b", tail[:6000]):
            problems.append(f"standalone_tick no longer names {name}; this gate guards a constant nothing reads")
    return problems


# --------------------------------------------------------------------------------------------
# Image half
# --------------------------------------------------------------------------------------------


def _main_checkout():
    """The main checkout's root when this file lives in a linked worktree, else None."""
    git = REPO / ".git"
    if not git.is_file():
        return None
    text = git.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        return None
    private = Path(text.split(":", 1)[1].strip())
    common = private / "commondir"
    if not common.is_file():
        return None
    return (private / common.read_text(encoding="utf-8").strip()).resolve().parent


def image_path():
    """`ER_DEOBF_BIN_1172`, else the repo's copy, else the main checkout's (images are gitignored)."""
    named = os.environ.get(IMAGE_ENV)
    if named:
        return Path(named)
    for root in (REPO, _main_checkout()):
        if root and (root / IMAGE_NAME).is_file():
            return root / IMAGE_NAME
    return REPO / IMAGE_NAME


def reference_counts(image, targets):
    """`{rva: loads}` -- how many `mov r64,[rip+disp]` in `image` load each of `targets`.

    One C-speed regex over the image rather than a search per target: the three-byte opcode
    prefixes occur hundreds of thousands of times in a 98 MB image.
    """
    counts = {rva: 0 for rva in targets}
    for match in MOV_RIP_SCAN.finditer(image):
        at = match.start()
        (displacement,) = struct.unpack_from("<i", image, at + DISPLACEMENT_AT)
        target = at + MOV_RIP_LENGTH + displacement
        if target in counts:
            counts[target] += 1
    return counts


def literal_around(image, rva):
    """The printable-ASCII run straddling `rva`, when it is long enough to be a string literal."""
    lo = rva
    while lo > 0 and rva - lo < 64 and 0x20 <= image[lo - 1] < 0x7F:
        lo -= 1
    hi = rva
    while hi < len(image) and hi - rva < 64 and 0x20 <= image[hi] < 0x7F:
        hi += 1
    run = image[lo:hi]
    return run.decode("ascii") if len(run) >= LITERAL_RUN else None


def pointer_global_problems(image, sections, rva, references):
    """Why `rva` is not a singleton pointer global in `image`, as a list; empty when it is."""
    problems = []
    section, executable = function_extent.classify(sections, IMAGE_BASE + rva)
    if section == "<outside>":
        return [f"0x{rva:x} is outside every section of the image"]
    if executable:
        problems.append(f"0x{rva:x} is in {section}, which is executable -- code, not a global")
    if rva % 8:
        problems.append(f"0x{rva:x} is not 8-byte aligned")
    if rva + 8 <= len(image):
        resting = struct.unpack_from("<Q", image, rva)[0]
        if resting:
            problems.append(
                f"0x{rva:x} is not zero at rest (0x{resting:016x}) -- a pointer global is "
                "uninitialised in the static image, so this is initialised data, not a slot"
            )
    run = literal_around(image, rva)
    if run is not None:
        problems.append(f"0x{rva:x} sits inside the string literal {run!r}")
    if references < MIN_REFERENCES:
        problems.append(
            f"0x{rva:x} is loaded by {references} `mov r64,[rip+disp]` instruction(s), fewer than "
            f"{MIN_REFERENCES}; the game's own code does not read it as a pointer"
        )
    return problems


def image_findings(image, values):
    sections = function_extent.sections(image)
    counts = reference_counts(image, set(values.values()))
    problems = []
    for name, rva in values.items():
        for problem in pointer_global_problems(image, sections, rva, counts[rva]):
            problems.append(f"{name}: {problem}")
    return problems, counts


# --------------------------------------------------------------------------------------------
# selftest and main
# --------------------------------------------------------------------------------------------

FIXTURE = {
    "crates/a/src/lib.rs": (
        "pub const AGREED_RVA: usize = 0x3d64fb8;\n"
        "pub const DRIFTED_RVA: usize = 0x3d64fb8;\n"
    ),
    "crates/b/src/lib.rs": (
        "const AGREED_RVA: usize = 0x3d6_4fb8;\n"
        "const DRIFTED_RVA: usize = 0x3d5df38;\n"
    ),
}


def selftest() -> int:
    import tempfile

    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # -- source half on a fixture: agreement passes, drift and absence fail ----------------------
    scratch = Path(tempfile.mkdtemp())
    for name, body in FIXTURE.items():
        (scratch / name).parent.mkdir(parents=True, exist_ok=True)
        (scratch / name).write_text(body, encoding="utf-8")
    fixture = rva_symbols.Index.build(root=str(scratch / "crates"))
    values, problems = singleton_values(fixture, ("AGREED_RVA", "DRIFTED_RVA", "ABSENT_RVA"))
    check("two declarations with one value agree", values.get("AGREED_RVA"), 0x3D64FB8)
    check("two declarations with different values are a finding", any("DRIFTED_RVA" in p for p in problems), True)
    check("an undeclared singleton is a finding", any("ABSENT_RVA" in p for p in problems), True)
    check("...and nothing else is", len(problems), 2)

    # -- the tick still reads what this gate guards ---------------------------------------------
    real_tick = TICK_SOURCE.read_text(encoding="utf-8")
    check("standalone_tick reads every guarded singleton through game_data_addr", tick_problems(real_tick), [])
    blinded = real_tick.replace("CS_FLIPPER_SINGLETON_RVA", "SOME_OTHER_RVA")
    check(
        "a tick that stopped naming a singleton is reported",
        any("CS_FLIPPER_SINGLETON_RVA" in p for p in tick_problems(blinded)),
        True,
    )

    # -- the real tree's values ------------------------------------------------------------------
    live = rva_symbols.index(str(REPO / "crates"))
    real, real_problems = singleton_values(live)
    check("every oracle singleton has one agreed value in the real tree", real_problems, [])
    check("...all four of them", sorted(real), sorted(ORACLE_SINGLETONS))

    # -- image half: real singletons pass, each planted address fails for its own reason ---------
    path = image_path()
    if path.is_file():
        image = path.read_bytes()
        sections = function_extent.sections(image)
        found, counts = image_findings(image, real)
        check("the four live singletons are pointer globals in the 2.7.2.0 image", found, [])
        control = literal_around(image, LITERAL_CONTROL_RVA)
        check(
            "the literal detector reads the RTTI name the stale 1.17 GameDataMan read landed in",
            control is not None and LITERAL_CONTROL_TEXT in control,
            True,
        )
        planted = reference_counts(image, {LITERAL_CONTROL_RVA, UNREFERENCED_CONTROL_RVA, CODE_CONTROL_RVA})
        for rva, reason in (
            (LITERAL_CONTROL_RVA, "string literal"),
            (UNREFERENCED_CONTROL_RVA, "does not read it as a pointer"),
            (CODE_CONTROL_RVA, "executable"),
            (real["GAME_DATA_MAN_GLOBAL_RVA"] + 4, "not 8-byte aligned"),
        ):
            why = " ".join(pointer_global_problems(image, sections, rva, planted.get(rva, counts.get(rva, 0))))
            check(f"a planted 0x{rva:x} is refused as {reason!r}", reason in why, True)
        print(
            "selftest: image half checked against "
            + f"{path.name} ("
            + ", ".join(f"{name} {counts[value]} loads" for name, value in sorted(real.items()))
            + ")"
        )
    else:
        print(f"selftest: image half skipped -- {path} is absent (set {IMAGE_ENV})")

    for line in failures:
        print(f"SELFTEST FAIL: {line}")
    if failures:
        return 1
    print("selftest: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true", help="prove the assertions can fail")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    problems = tick_problems(TICK_SOURCE.read_text(encoding="utf-8"))
    values, source = singleton_values(rva_symbols.index(str(REPO / "crates")))
    problems += source
    path = image_path()
    if path.is_file():
        image, counts = image_findings(path.read_bytes(), values)
        problems += image
        print(
            "image half: "
            + ", ".join(f"{name} 0x{value:x} ({counts[value]} loads)" for name, value in sorted(values.items()))
        )
    else:
        print(f"image half skipped: {path} is absent (set {IMAGE_ENV} to run it)")
    for problem in problems:
        print(f"  {problem}")
    if problems:
        print(f"\n{len(problems)} problem(s)")
        return 1
    print(f"oracle singletons: {len(values)} agreed in source" + (" and corroborated in the image" if path.is_file() else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
