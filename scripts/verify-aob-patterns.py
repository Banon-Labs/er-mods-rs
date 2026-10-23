#!/usr/bin/env python3
"""Re-validate every runtime byte signature, AOB scan pattern and prologue pin against 2.7.2.0.

Why this exists
---------------
A scan has no build gate.  It runs against whatever image is loaded and answers with an address,
and there are three ways that answer goes wrong:

  (a) it matches nothing          -> the feature is refused.  Loud and safe.
  (b) it matches a different site -> a confident wrong address that reads as a live value.
  (c) it matches more than once where it used to be unique -> "first match wins" picks silently.

Only (a) is safe, and a bare hit count cannot tell the three apart, so every pattern's count on
the supported image is pinned here and a drift in either direction fails.

The workspace supports one game build, ELDEN RING FileVersion 2.7.2.0, and every check below reads
its de-Arxan'd flat image, `eldenring-deobf-1.17.2.bin` (located by `scripts/er_game_image.py`:
`$ER_DEOBF_BIN_1172`, then this checkout and every directory above it).

What it checks
--------------
  sync      Every pinned pattern literal is re-extracted from the source file it lives in, so an
            edit to the constant in the crate fails here until the pin is updated.  Needs no
            image and always runs.
  patterns  Every literal AOB in the tree, scanned over the image's first `.text` (the section
            the runtime scanners bound themselves to -- see the note on `.text` below), with its
            hit count compared against the pinned count.
  needles   Pointer-value scans whose needle is built at runtime from an rva constant.
            `er_game_base::mem::game_data_addr` answers 0 on any build other than 2.7.2.0.  A zero
            used as a dereference target faults closed; a zero used as a scan needle matches every
            zeroed qword in the address space, so the scan returns a confident pointer into blank
            memory.  Each scan must reject a zero needle, its needle constants must still hold the
            pinned rva, and a vtable needle must still name its pinned RTTI class in the image.
  prologues The detour-prologue pins `build-support/prologue_build.rs` generates, plus
            er-invasion-warp's hand-written `MapSeam` table.  Each is compared against the image at
            its own literal VA, the address the running game is read at.  A spec this parser
            cannot read, or an `Image` variant it does not model, is a hard failure.

`patterns`, `needles` and `prologues` skip when the image is absent (the same shape as the
corpus-gated tests), unless `--require-images` is passed.  The selftest needs no image.

The note on `.text`
-------------------
The image carries two sections named `.text`: the real code at 0x140001000, and an Arxan tail at
the very top.  Every runtime scanner in this repo resolves the section by walking the PE section
table and returning the first `.text` (`er_game_base::mem::module_text_range`, and
`find_text_section` in er-quickload's crashlog), so that is the range scanned here.

Usage
    python3 scripts/verify-aob-patterns.py                 # everything
    python3 scripts/verify-aob-patterns.py --section needles
    python3 scripts/verify-aob-patterns.py --section prologues
    python3 scripts/verify-aob-patterns.py --selftest
    python3 scripts/verify-aob-patterns.py --require-images

Exit code 0 = every pattern matched its pinned count, every needle scan is guarded and every pin
arms; 1 = a drift, a bad pin, an unguarded scan or an unreadable spec.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import er_game_image  # noqa: E402 - repo-local, and the sys.path line above is what makes it work

ROOT = er_game_image.REPO
BASE = er_game_image.BASE
GENERATOR = ROOT / "build-support/prologue_build.rs"

# --- which module a prologue lives in ----------------------------------------------------------
# `build-support/prologue_build.rs` declares `enum Image`, and a `PrologueSpec` names one variant.
# This table says what each variant means here: `"game"` for the 2.7.2.0 image this script loads,
# or `None` for a real variant whose module this script does not load.
#
# It is checked for completeness against the enum at run time, and a variant the enum declares and
# this table does not model is a hard failure rather than a skip.  That rule exists because the
# absence of it once cost three pins: a new variant was added, the dispatch here compared against
# the old one, and three pins that armed were filed as skip in a gate whose exit code stayed 0.  A
# skip inside a green gate is indistinguishable from a check that ran.
IMAGE_ROLE = {
    "EldenRing2720": "game",
    # Seamless Co-op's `ersc.dll`.  A real image with real pins, ground-truthed by the build script
    # against the supported `ersc.dll`; this script loads only the game image and says so.
    "Ersc201": None,
}
_UNMODELLED = object()

# --- pattern registry -------------------------------------------------------------------------
# `symbol` names the Rust constant the literal is extracted from, so the pinned copy below can
# never drift from the code without `sync` saying so.  `literals` picks which string literals in
# that constant's initialiser are find patterns (a table of (find, patch) pairs has both).
#
# `consumer` is what the calling code does with the result, which is what decides whether a
# non-unique answer is a lost feature or a corruption:
#   unique-or-refuse    the scanner returns None on 0 or >1 hits and the caller disarms.
#   patch-every-match   every hit is written to.  The count is what stands between this and
#                       overwriting unrelated code.
#
# `expect` is the hit count in the first `.text` of `eldenring-deobf-1.17.2.bin`, measured
# 2026-10-23.  The same counts held on 1.16.2 and 1.17.0, which is the calibration that says none
# of these patterns was fragile before the port.
REGISTRY = [
    {
        "id": "armament-icons/PARSE_SIG",
        "source": "crates/er-armament-icons/src/gfx_equip_hook.rs",
        "symbol": "PARSE_SIG",
        "literals": [0],
        "consumer": "unique-or-refuse",
        "pattern": "40 53 48 83 EC 40 48 8B 41 18 48 8B D9 C6 44 24 30 01 48 83 C1 50 4C 8B 50 20 4C 8B 58 48",
        "expect": 1,
        "first": 0x1411D2700,
        "note": "GFx tag-parse entry; hooked. scan_unique() refuses 0 or >1.",
    },
    {
        "id": "invasion-warp/PARSE_SIG",
        "source": "crates/er-invasion-warp/src/map_gfx.rs",
        "symbol": "PARSE_SIG",
        "literals": [0],
        "consumer": "unique-or-refuse",
        "pattern": "40 53 48 83 EC 40 48 8B 41 18 48 8B D9 C6 44 24 30 01 48 83 C1 50 4C 8B 50 20 4C 8B 58 48",
        "expect": 1,
        "first": 0x1411D2700,
        "note": "Same function as armament-icons/PARSE_SIG; independent copy of the literal.",
    },
    {
        "id": "quickload/ANTI_ANTIDEBUG check1",
        "source": "crates/er-quickload/src/constants/anti_debug.rs",
        "symbol": "ANTI_ANTIDEBUG_CHECKS",
        "literals": [0],
        "consumer": "patch-every-match",
        "pattern": "7A ?? 75 ?? B9 ?? ?? ?? ?? E8 ?? ?? ?? ?? F3 0F 11 05",
        "expect": 181,
        "first": 0x1401ECF5F,
        "note": "ProDebug port. Every hit is patched; the count is the safety property.",
    },
    {
        "id": "quickload/ANTI_ANTIDEBUG check2",
        "source": "crates/er-quickload/src/constants/anti_debug.rs",
        "symbol": "ANTI_ANTIDEBUG_CHECKS",
        "literals": [2],
        "consumer": "patch-every-match",
        "pattern": "0F 8A ?? ?? ?? ?? 0F 85 ?? ?? ?? ?? B9 ?? ?? ?? ?? E8 ?? ?? ?? ?? F3 0F 11 05",
        "expect": 1,
        "first": 0x140D71C63,
        "note": "ProDebug port, long form of check1.",
    },
    {
        "id": "quickload/ANTI_ANTIDEBUG check3",
        "source": "crates/er-quickload/src/constants/anti_debug.rs",
        "symbol": "ANTI_ANTIDEBUG_CHECKS",
        "literals": [4],
        "consumer": "patch-every-match",
        "pattern": "73 ?? 0F 2F ?? 76 ?? 48 8D 15",
        "expect": 138,
        "first": 0x1401CCB70,
        "note": "ProDebug port. 10 bytes -- the shortest signature in the tree.",
    },
    {
        "id": "quickload/ANTI_ANTIDEBUG check4",
        "source": "crates/er-quickload/src/constants/anti_debug.rs",
        "symbol": "ANTI_ANTIDEBUG_CHECKS",
        "literals": [6],
        "consumer": "patch-every-match",
        "pattern": "72 ?? 48 8D 4C 24 ?? E8 ?? ?? ?? ?? 90 48 8B 05 ?? ?? ?? ?? FF D0",
        "expect": 10,
        "first": 0x1403AA105,
        "note": "ProDebug port; patches one byte (72 -> EB) at each hit.",
    },
]


# --- runtime-built needle registry ------------------------------------------------------------
# These scans compare a qword read out of live memory against `base + rva`, resolved through
# `game_data_addr`, which answers 0 on any other build.  `guard` is the source regex that proves
# the code rejects a zero needle.
#
# Each needle is `(constant, 2.7.2.0 rva, RTTI class or None, file, definition regex)`.  The regex
# has one group, the literal the constant is defined as; for a vtable the image must carry the
# named class's CompleteObjectLocator at `[rva-8]`, and `None` is a plain `.data` table, which is
# zeros at rest and has no content to check, so only its section is.
_TITLE_VTABLE = r"TitleOwnerVtable\s*=\s*(0x[0-9a-fA-F_]+)"
_TITLE_TABLE = r"const\s+INNER_TITLE_STATE_TABLE_RVA\s*:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)"
_PLAIN_VTABLE = r"const\s+TITLE_OWNER_VTABLE_RVA\s*:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)"
NEEDLE_SCANS = [
    {
        "id": "telemetry-core/title_binding::scan_for_owner",
        "source": "crates/er-telemetry-core/src/read/title_binding.rs",
        "needles": [
            ("TITLE_OWNER_VTABLE_RVA", 0x2B68D40, ".?AVTitleStep@CS@@",
             "crates/er-telemetry-core/src/read/title_binding.rs", _PLAIN_VTABLE),
            ("INNER_TITLE_STATE_TABLE_RVA", 0x3D78610, None,
             "crates/er-telemetry-core/src/read/title_binding.rs", _TITLE_TABLE),
        ],
        "guard": r"want_vtable\s*==\s*0|want_vtable\s*<\s*HEAP_LO",
        "consumes": "owner pointer cached forever; fields at +0x48/+0xe0/+0x130 read and reported "
        "as title-binding telemetry",
    },
    {
        "id": "input-harness/title_scan::scan_for_owner",
        "source": "crates/er-input-harness/src/title_scan.rs",
        "needles": [
            ("TITLE_OWNER_VTABLE_RVA", 0x2B68D40, ".?AVTitleStep@CS@@",
             "crates/er-input-harness/src/title_scan.rs", _PLAIN_VTABLE),
            ("INNER_TITLE_STATE_TABLE_RVA", 0x3D78610, None,
             "crates/er-input-harness/src/title_scan.rs", _TITLE_TABLE),
        ],
        "guard": r"want_vtable\s*==\s*0|want_vtable\s*<\s*HEAP_LO",
        "consumes": "title_state()/title_dialog_a40() gate the harness's menu drive",
    },
    {
        "id": "title-flow/profile_select_flow::find_title_owner_by_vtable",
        "source": "crates/er-title-flow/src/profile_select_flow.rs",
        "needles": [
            ("TITLE_OWNER_VTABLE_RVA", 0x2B68D40, ".?AVTitleStep@CS@@",
             "crates/er-title-flow/src/constants_moved.rs", _TITLE_VTABLE),
            ("INNER_TITLE_STATE_TABLE_RVA", 0x3D78610, None,
             "crates/er-title-flow/src/constants_moved.rs",
             r"const\s+INNER_TITLE_STATE_TABLE_RVA\s*:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)"),
        ],
        "guard": r"target_vtable\s*==\s*0|target_vtable\s*<\s*HEAP_LO",
        "consumes": "the title owner the product's own title/load stepper drives; TITLE_OWNER_MIN_STATE "
        "is 0, so a zeroed block passes the state cross-check too",
    },
    {
        "id": "telemetry-core/dialog_active::scan_for_dialog",
        "source": "crates/er-telemetry-core/src/read/dialog_active.rs",
        "needles": [
            ("MSGBOX_DIALOG_VTABLE_RVA", 0x2B086C0, ".?AVMessageBoxDialog@CS@@",
             "crates/er-game-base/src/rva.rs", r"\bDialogVtable\s*=\s*(0x[0-9a-fA-F_]+)"),
            ("SAVE_RETRY_DIALOG_VTABLE_RVA", 0x2AAFD68, ".?AVSaveRetryDialog@CS@@",
             "crates/er-telemetry-core/src/read/dialog_active.rs",
             r"const\s+SAVE_RETRY_DIALOG_VTABLE_RVA\s*:\s*usize\s*=\s*(0x[0-9a-fA-F_]+)"),
        ],
        "guard": r"if\s+vt\s*==\s*0\s*\{",
        "consumes": "msgbox-active oracle; dialog_class() rejects vt == 0 before anything else",
    },
]


# --- matching primitives ----------------------------------------------------------------------
def parse_sig(sig):
    """IDA-style signature -> (bytes, mask). `?` and `??` are wildcards."""
    want, mask = [], []
    for token in sig.split():
        if token in ("?", "??"):
            want.append(0)
            mask.append(False)
        else:
            want.append(int(token, 16))
            mask.append(True)
    if not want:
        raise ValueError("empty signature")
    if not any(mask):
        raise ValueError("signature is entirely wildcards")
    return bytes(want), mask


def find_all(data, start, size, want, mask, cap=4096):
    """Every offset in [start, start+size) matching (want, mask), overlapping matches included."""
    n = len(want)
    anchor = next(i for i, keep in enumerate(mask) if keep)
    needle = bytes([want[anchor]])
    hits = []
    end = start + size
    cursor = start
    while len(hits) < cap:
        at = data.find(needle, cursor, end)
        if at < 0:
            break
        off = at - anchor
        cursor = at + 1
        if off < start or off + n > end:
            continue
        if all(not mask[j] or data[off + j] == want[j] for j in range(n)):
            hits.append(off)
    return hits


def load_image(require_images, section):
    """The 2.7.2.0 image, or `None` with the skip printed (and counted when images are required)."""
    path = er_game_image.image_path()
    if path is None:
        print(f"== {section}: SKIPPED -- {er_game_image.missing_reason()}")
        return None, (1 if require_images else 0)
    return er_game_image.FlatImage.load(path), 0


# --- source extraction (the anti-drift half) --------------------------------------------------
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def extract_literals(path, symbol):
    """Every string literal in the initialiser of `const`/`static` `symbol` in `path`."""
    src = path.read_text(encoding="utf-8", errors="replace")
    decl = re.search(r"(?:const|static)\s+" + re.escape(symbol) + r"\s*:", src)
    if not decl:
        raise KeyError(f"{path}: no const/static named {symbol}")
    # The initialiser runs to the first `;` at nesting depth 0 after the `=`.
    eq = src.index("=", decl.end())
    depth = 0
    i = eq
    while i < len(src):
        ch = src[i]
        if ch == '"':  # skip a string literal wholesale
            m = _STRING.match(src, i)
            i = m.end() if m else i + 1
            continue
        if ch in "[({":
            depth += 1
        elif ch in "])}":
            depth -= 1
        elif ch == ";" and depth == 0:
            break
        i += 1
    return [m.group(1) for m in _STRING.finditer(src[eq:i])]


def check_sync():
    """Every pinned pattern still matches the literal in its source file."""
    bad = 0
    print("== sync: pinned literals vs. the crates they live in ==")
    for entry in REGISTRY:
        path = ROOT / entry["source"]
        try:
            literals = extract_literals(path, entry["symbol"])
        except (KeyError, OSError, ValueError) as exc:
            print(f"  DRIFT {entry['id']}: {exc}")
            bad += 1
            continue
        for index in entry["literals"]:
            if index >= len(literals):
                print(
                    f"  DRIFT {entry['id']}: {entry['symbol']} has {len(literals)} literal(s), "
                    f"wanted index {index}"
                )
                bad += 1
                continue
            if literals[index] != entry["pattern"]:
                print(f"  DRIFT {entry['id']}: source says\n      {literals[index]}\n    pin says\n      {entry['pattern']}")
                bad += 1
            else:
                print(f"  ok    {entry['id']}  ({entry['symbol']}[{index}] in {entry['source']})")
    return bad


# --- classification ---------------------------------------------------------------------------
def classify(count):
    if count == 1:
        return "UNIQUE"
    if count == 0:
        return "ZERO"
    return "MULTI"


def pattern_verdict(entry, hits):
    """`(problems, verdict)` for one registry entry against its measured hit list."""
    count = len(hits)
    verdict = classify(count)
    problems = []
    if count != entry["expect"]:
        problems.append(f"REGRESSION: pinned {entry['expect']} hit(s), measured {count}")
    if entry["consumer"] == "unique-or-refuse" and verdict == "MULTI":
        problems.append("a unique-or-refuse scan matches more than once: the feature is refused")
    if entry["consumer"] == "patch-every-match" and verdict == "ZERO":
        problems.append("a patch-every-match scan matches nothing: the patch silently does nothing")
    if hits and entry.get("first") is not None and BASE + hits[0] != entry["first"]:
        problems.append(f"first hit moved: pinned {entry['first']:#x}, measured {BASE + hits[0]:#x}")
    return problems, verdict


def run_patterns(require_images):
    image, skipped = load_image(require_images, "patterns")
    if image is None:
        return skipped
    print("== patterns: literal AOB signatures over the runtime-scanned .text ==")
    primary, tail = image.text[0], image.text[1] if len(image.text) > 1 else None
    note = (
        f"  tail .text {BASE + tail[0]:#x}+{tail[1]:#x} (Arxan; not scanned at runtime)"
        if tail
        else ""
    )
    print(f"  runtime .text {BASE + primary[0]:#x}+{primary[1]:#x}{note}")
    bad = 0
    for entry in REGISTRY:
        want, mask = parse_sig(entry["pattern"])
        start, size = image.text[0]
        hits = find_all(image.data, start, size, want, mask)
        problems, verdict = pattern_verdict(entry, hits)
        print(f"\n  {entry['id']}  [{entry['consumer']}]  {len(want)} bytes")
        print(f"    hits={len(hits):<5d} -> {verdict}")
        show = ", ".join(hex(BASE + h) for h in hits[:4])
        print(f"    first hits: {show or '(none)'}")
        print(f"    {entry['note']}")
        for line in problems:
            print(f"    {line}")
        bad += len(problems)
    return bad


# --- needle audit -----------------------------------------------------------------------------
def needle_value(file, pattern):
    """The literal `pattern` captures in `file`, or `None` when the definition is gone."""
    path = ROOT / file
    if not path.is_file():
        return None
    match = re.search(pattern, path.read_text(encoding="utf-8", errors="replace"))
    return int(match.group(1).replace("_", ""), 16) if match else None


def needle_findings(scan, image, read_value=needle_value):
    """Problems for one needle scan. `image` may be `None`, which checks the source half only."""
    problems = []
    path = ROOT / scan["source"]
    src = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    if not re.search(scan["guard"], src):
        problems.append(
            "no zero-needle guard in source: on any build but 2.7.2.0 the needle is 0 and this "
            "scan returns a pointer into zeroed memory"
        )
    for name, rva, rtti, file, pattern in scan["needles"]:
        value = read_value(file, pattern)
        if value is None:
            problems.append(f"{name}: its definition is no longer in {file}")
        elif value != rva:
            problems.append(f"{name}: {file} says {value:#x}, this gate pins {rva:#x}")
        if image is None:
            continue
        if rtti is not None:
            found = image.rtti_class(rva)
            if found is None or found[1] != rtti:
                problems.append(
                    f"{name} {rva:#x}: the image carries {found and found[1]!r} there, not the "
                    f"vtable of {rtti}"
                )
        else:
            section = image.section_of(rva)
            if section is None or section[0] != ".data":
                problems.append(f"{name} {rva:#x}: not in .data ({section and section[0]})")
    return problems


def run_needles(require_images):
    image, skipped = load_image(require_images, "needles (image half)")
    print("== needles: scans whose pattern is built at runtime from an rva constant ==")
    print("   game_data_addr() answers 0 on any other build. A zero dereference faults closed;")
    print("   a zero scan needle matches every zeroed qword in the address space.")
    bad = skipped
    for scan in NEEDLE_SCANS:
        print(f"\n  {scan['id']}")
        print(f"    {scan['source']}")
        for name, rva, rtti, _file, _pattern in scan["needles"]:
            print(f"    needle {name} = {rva:#x}  {rtti or '(.data table)'}")
        print(f"    result feeds: {scan['consumes']}")
        problems = needle_findings(scan, image)
        for line in problems:
            print(f"    {line}")
        bad += len(problems)
    return bad


# --- prologue-gate audit ----------------------------------------------------------------------
# `build-support/prologue_build.rs` generates one byte constant per detour target, pinned to the
# bytes 2.7.2.0 has at that VA.  At runtime the crate re-reads the live bytes and disarms on a
# mismatch; the build script ground-truths the same pin when the image is present.  This section
# is the offline copy of that comparison, which runs without a build.
_FIELD = {
    "name": re.compile(r'name:\s*"([A-Za-z0-9_]+)"'),
    "image": re.compile(r"image:\s*Image::(\w+)"),
    "va": re.compile(r"va:\s*([A-Za-z0-9_]+)\s*,"),
    # Either an inline `&[0x.., ..]` or the name of a `const .._PIN: &[u8]` in the same build.rs.
    # Both spellings are in the tree, and only the first used to be read: four specs share a named
    # pin, and they were dropped by the parser and never appeared in the report at all.
    # `prologue_spec_gaps()` is what makes that impossible to repeat.
    "pin": re.compile(r"pin:\s*(&\[.*?\]|[A-Z][A-Z0-9_]*)\s*,", re.DOTALL),
}
_HEXBYTE = re.compile(r"0x([0-9A-Fa-f]{1,2})")


def _spec_bodies(src):
    """Every `PrologueSpec { .. }` body, brace-matched.

    A non-greedy `\\{(.*?)\\}\\s*,` stops at the first `}` followed by a comma, so one nested
    struct literal inside a spec would truncate the body, drop the fields after it, and silently
    lose the spec. Counting braces cannot do that, and the count it yields is what
    `prologue_spec_gaps()` compares against.
    """
    bodies = []
    for match in re.finditer(r"PrologueSpec\s*\{", src):
        start = src.index("{", match.start())
        depth = 0
        for index in range(start, len(src)):
            if src[index] == "{":
                depth += 1
            elif src[index] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(src[start + 1 : index])
                    break
        else:
            bodies.append(src[start + 1 :])  # unbalanced source; the gap check will catch it
    return bodies


def _build_scripts():
    return [
        path
        for path in sorted((ROOT / "crates").glob("*/build.rs"))
        if "PrologueSpec" in path.read_text(encoding="utf-8", errors="replace")
    ]


# A `va:` need not be a hex literal in the build script itself. `check-rva-alias-drift.py` requires
# a game address to be declared exactly once, so a crate whose window starts at its own function
# entry cannot spell it a second time here -- er-convenient-deaths keeps its addresses in an
# `include!`d file and derives each `va` as `BASE + (ENTRY + OFFSET) as u64`. The two forms below
# read that without loosening anything: an unresolvable name still yields None, which still lands
# the spec in `prologue_spec_gaps()` as a hard failure.
_INCLUDE = re.compile(r'include!\(concat!\(env!\("CARGO_MANIFEST_DIR"\),\s*"/([^"]+)"\)\)')
_CONST_LITERAL = re.compile(r"const\s+([A-Z0-9_]+):\s*(?:u64|usize)\s*=\s*(0x[0-9a-fA-F_]+|\d+)\s*;")
_CONST_EXPR = re.compile(r"const\s+([A-Z0-9_]+):\s*u64\s*=\s*([^;]+);")
_TERM = re.compile(r"^(?:0x[0-9a-fA-F_]+|\d+|[A-Z][A-Z0-9_]*)$")


def _sum_of_known(expr, known):
    """Value of `A + (B + C) as u64`, or None if any term is not already known.

    Deliberately only addition of literals and named constants. Anything else -- a call, a shift,
    a subtraction -- returns None and the spec is reported as a gap.
    """
    cleaned = re.sub(r"\bas\s+\w+", " ", expr).replace("(", " ").replace(")", " ")
    total = 0
    for term in cleaned.split("+"):
        term = term.strip()
        if not _TERM.match(term or ""):
            return None
        if term[:2].lower() == "0x" or term.isdigit():
            total += int(term.replace("_", ""), 0)
        elif term in known:
            total += known[term]
        else:
            return None
    return total


def _constants(build):
    """Every named integer constant a build script can see, its own and any it `include!`s."""
    src = build.read_text(encoding="utf-8", errors="replace")
    text = src
    for match in _INCLUDE.finditer(src):
        included = build.parent / match.group(1)
        if included.is_file():
            text += "\n" + included.read_text(encoding="utf-8", errors="replace")
    known = {m.group(1): int(m.group(2).replace("_", ""), 0) for m in _CONST_LITERAL.finditer(text)}
    # A derived constant can name another derived constant, so keep folding until nothing new
    # resolves rather than assuming declaration order.
    for _ in range(8):
        added = False
        for match in _CONST_EXPR.finditer(text):
            name, expr = match.group(1), match.group(2)
            if name in known:
                continue
            value = _sum_of_known(expr, known)
            if value is not None:
                known[name] = value
                added = True
        if not added:
            break
    return known


def _parse_spec(body, crate, consts, pins):
    fields = {}
    for key, rx in _FIELD.items():
        found = rx.search(body)
        if not found:
            return None
        fields[key] = found.group(1)
    literal = fields["pin"]
    if literal.startswith("&["):
        pin = bytes(int(b, 16) for b in _HEXBYTE.findall(literal))
    else:
        if literal not in pins:
            return None
        pin = pins[literal]
    return (crate, fields["name"], fields["image"], consts.get(fields["va"]), fields["va"], pin)


def _named_pins(src):
    return {
        m.group(1): bytes(int(b, 16) for b in _HEXBYTE.findall(m.group(2)))
        for m in re.finditer(
            r"(?:const|static)\s+([A-Z][A-Z0-9_]*):\s*&\[u8\]\s*=\s*&\[(.*?)\]\s*;",
            src,
            re.DOTALL,
        )
    }


def prologue_specs():
    """(crate, name, image, va, va_symbol, pin_bytes) for every PrologueSpec in the workspace."""
    out = []
    for build in _build_scripts():
        src = build.read_text(encoding="utf-8", errors="replace")
        consts = _constants(build)
        pins = _named_pins(src)
        for body in _spec_bodies(src):
            parsed = _parse_spec(body, build.parent.name, consts, pins)
            if parsed is not None:
                out.append(parsed)
    return out


def prologue_spec_gaps():
    """`[(crate, name_or_'<unnamed>')]` for every declared PrologueSpec this parser could not read.

    A spec the parser drops produces no row and no tally entry, so the report is short by one and
    says nothing about it. That is strictly worse than a skip. Any gap is a hard failure: an audit
    that cannot enumerate its own subjects is not an audit.
    """
    gaps = []
    for build in _build_scripts():
        src = build.read_text(encoding="utf-8", errors="replace")
        consts = _constants(build)
        pins = _named_pins(src)
        for body in _spec_bodies(src):
            if _parse_spec(body, build.parent.name, consts, pins) is not None:
                continue
            named = _FIELD["name"].search(body)
            gaps.append((build.parent.name, named.group(1) if named else "<unnamed>"))
    return gaps


def declared_image_variants(path=GENERATOR):
    """Every variant of `enum Image` the prologue generator declares."""
    src = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"enum Image \{(.*?)\n\}", src, re.DOTALL)
    if not match:
        raise SystemExit(f"{path}: could not find `enum Image` -- the generator's shape moved")
    body = re.sub(r"///[^\n]*", "", match.group(1))
    return [name for name in re.findall(r"^\s*([A-Z][A-Za-z0-9_]*)\s*,", body, re.MULTILINE)]


def unmodelled_image_variants():
    """Variants the generator declares that `IMAGE_ROLE` does not describe."""
    return [name for name in declared_image_variants() if name not in IMAGE_ROLE]


_SEAM = re.compile(
    r'name:\s*"(?P<name>[^"]+)"\s*,\s*rva:\s*(?P<rva>0x[0-9a-fA-F_]+)\s*,\s*'
    r"prologue:\s*&\[(?P<pin>.*?)\]",
    re.DOTALL,
)


def seam_specs():
    """(crate, name, image, va, va_symbol, pin) for the hand-written `MapSeam` prologue table.

    `er-invasion-warp`'s map seams predate `prologue_build.rs` and carry their expected bytes
    inline beside a 2.7.2.0 rva.  They are checked by `verify_seam` at exactly the same moment and
    with exactly the same fail-closed consequence as a generated prologue (compared with `!=`, no
    mask), so leaving them out would report the crate as clean while its hooks silently disarmed.
    """
    path = ROOT / "crates/er-invasion-warp/src/map_seams.rs"
    if not path.is_file():
        return []
    src = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for match in _SEAM.finditer(src):
        rva = int(match.group("rva").replace("_", ""), 16)
        pin = bytes(int(b, 16) for b in _HEXBYTE.findall(match.group("pin")))
        out.append(
            ("er-invasion-warp (map_seams)", match.group("name"), "EldenRing2720", BASE + rva, "inline", pin)
        )
    return out


VERDICTS = ("ARMS", "BAD-PIN", "SKIP", "UNKNOWN-IMAGE")


def classify_prologue(spec, data):
    """`(verdict, detail)` for one spec against the flat image bytes `data`.

    The one place an `Image` variant is dispatched. `data` is the image's bytes; the selftest
    passes synthetic ones, so every branch below is reachable without the game files.
    """
    _crate, _name, image_kind, va, va_sym, pin = spec
    role = IMAGE_ROLE.get(image_kind, _UNMODELLED)
    if role is _UNMODELLED:
        # Not a skip. A variant nobody taught this script about is a pin nobody is checking.
        return "UNKNOWN-IMAGE", (
            f"`Image::{image_kind}` is not in IMAGE_ROLE -- teach this script the variant "
            "instead of letting its pins go unchecked"
        )
    if role is None:
        return "SKIP", f"{image_kind} module, which this script does not load"
    if va is None:
        return "SKIP", f"could not resolve {va_sym}"
    rva = va - BASE
    have = data[rva : rva + len(pin)]
    if len(have) != len(pin):
        return "BAD-PIN", f"{va:#x} is past the end of the image"
    if have == pin:
        return "ARMS", f"{va:#x}"
    differ = [i for i in range(len(pin)) if pin[i] != have[i]]
    return "BAD-PIN", (
        f"{va:#x} holds {have.hex()}, not the pin {pin.hex()} (byte(s) {differ}); the gate "
        "will disarm"
    )


def run_prologues(require_images):
    bad = 0
    unmodelled = unmodelled_image_variants()
    if unmodelled:
        print(
            "== prologues: the generator declares `Image` variant(s) this script does not model: "
            + ", ".join(unmodelled)
        )
        bad += len(unmodelled)
    gaps = prologue_spec_gaps()
    for crate, name in gaps:
        print(f"== prologues: UNPARSED PrologueSpec {crate}/{name} -- it has no verdict at all")
    bad += len(gaps)
    image, skipped = load_image(require_images, "prologues")
    if image is None:
        return bad + skipped
    specs = prologue_specs() + seam_specs()
    print("== prologues: byte gates compared against 2.7.2.0 at their own VA ==")
    print("   ARMS      the pin matches the image and the hook installs.")
    print("   BAD-PIN   the image holds other bytes -- fail-closed, the feature is lost.")
    tally = dict.fromkeys(VERDICTS, 0)
    rows = []
    for spec in specs:
        verdict, detail = classify_prologue(spec, image.data)
        tally[verdict] += 1
        rows.append((spec[0], spec[1], verdict, detail))
    width = max((len(r[1]) for r in rows), default=0)
    last_crate = None
    for crate, name, verdict, detail in rows:
        if crate != last_crate:
            print(f"\n  {crate}")
            last_crate = crate
        print(f"    {verdict:<13s} {name:<{width}s}  {detail}")
    print(
        "\n  totals: "
        + "  ".join(f"{k}={v}" for k, v in tally.items())
        + f"  (of {len(specs)} specs)"
    )
    return bad + tally["BAD-PIN"] + tally["UNKNOWN-IMAGE"]


# --- selftest ---------------------------------------------------------------------------------
def selftest():
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, wanted {want!r}")

    want, mask = parse_sig("48 ?? 8B ?")
    check("parse_sig bytes", want, bytes([0x48, 0, 0x8B, 0]))
    check("parse_sig mask", mask, [True, False, True, False])
    for bad_sig in ("", "?? ??"):
        try:
            parse_sig(bad_sig)
            failures.append(f"parse_sig({bad_sig!r}) should have raised")
        except ValueError:
            pass

    # A wildcard-leading signature must still anchor on a concrete byte.
    data = bytes.fromhex("00" * 4 + "AA48BB" + "00" * 4 + "CC48DD" + "00" * 4)
    want, mask = parse_sig("?? 48 ??")
    check("anchor past a leading wildcard", find_all(data, 0, len(data), want, mask), [4, 11])

    # Overlapping matches are all reported -- a scanner that skipped them would under-count and
    # a "unique" verdict would be wrong.
    data = b"\xaa\xaa\xaa\xaa"
    want, mask = parse_sig("AA AA")
    check("overlapping matches", find_all(data, 0, len(data), want, mask), [0, 1, 2])

    # A match must not straddle the end of the scanned range...
    data = b"\x00\x11\x22\x33"
    want, mask = parse_sig("33 44")
    check("no match past the range end", find_all(data, 0, len(data), want, mask), [])
    # ...nor start before it.
    data = b"\x11\x22\x33\x44"
    want, mask = parse_sig("11 22")
    check("range start respected", find_all(data, 2, 2, want, mask), [])

    check("classify unique", classify(1), "UNIQUE")
    check("classify zero", classify(0), "ZERO")
    check("classify multi", classify(3), "MULTI")

    # The pattern verdict has teeth in every direction a count can move.
    unique = dict(REGISTRY[0])
    check("a pinned unique count passes", pattern_verdict(unique, [unique["first"] - BASE])[0], [])
    check("a second hit fails", len(pattern_verdict(unique, [1, 2])[0]) >= 1, True)
    check("a lost hit fails", len(pattern_verdict(unique, [])[0]), 1)
    check("a moved hit fails", len(pattern_verdict(unique, [0x10])[0]), 1)
    shotgun = dict(REGISTRY[2])
    check("a patch-every-match count that drifts fails",
          len(pattern_verdict(shotgun, list(range(shotgun["expect"] - 1)))[0]) >= 1, True)

    # Source extraction handles a multi-line const and a table of tuples.
    sample = ROOT / "target" / "verify-aob-selftest.rs"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text(
        'const OTHER: &str = "not me";\n'
        "const SIG: &str =\n"
        '    "40 53 48";\n'
        "static TABLE: &[(&str, &str)] = &[\n"
        '    ("7A ?? 75", "?? 02"),\n'
        '    ("0F 8A ??", "?? ?? 06"),\n'
        "];\n",
        encoding="utf-8",
    )
    try:
        check("extract single literal", extract_literals(sample, "SIG"), ["40 53 48"])
        check(
            "extract table literals",
            extract_literals(sample, "TABLE"),
            ["7A ?? 75", "?? 02", "0F 8A ??", "?? ?? 06"],
        )
        try:
            extract_literals(sample, "NOPE")
            failures.append("extract_literals(NOPE) should have raised")
        except KeyError:
            pass
    finally:
        sample.unlink(missing_ok=True)

    # Every registry pattern parses, every registry source file exists, and sync is clean.
    for entry in REGISTRY:
        try:
            parse_sig(entry["pattern"])
        except ValueError as exc:
            failures.append(f"{entry['id']}: {exc}")
        if not (ROOT / entry["source"]).is_file():
            failures.append(f"{entry['id']}: missing source {entry['source']}")

    # The needle source half runs against the real tree with no image: every guard present and
    # every pinned rva still the one the crate defines.  Then the two ways it must go red.
    for scan in NEEDLE_SCANS:
        re.compile(scan["guard"])
        problems = needle_findings(scan, None)
        if problems:
            failures.append(f"{scan['id']}: {problems}")
    scan = NEEDLE_SCANS[0]
    check("a drifted needle constant is caught",
          len(needle_findings(scan, None, read_value=lambda _f, _p: 0x1234)) >= 1, True)
    check("a deleted needle definition is caught",
          len(needle_findings(scan, None, read_value=lambda _f, _p: None)) >= 1, True)
    unguarded = dict(scan, guard=r"this guard is nowhere in the source")
    check("a missing zero-needle guard is caught", len(needle_findings(unguarded, None)) >= 1, True)

    # The PrologueSpec parser must resolve a symbolic `va` and decode the pin, or the whole
    # prologue section silently reports nothing.
    specs = prologue_specs() + seam_specs()
    if not specs:
        failures.append("prologue_specs() found no PrologueSpec in crates/*/build.rs")
    unresolved = [f"{c}/{n}" for c, n, _i, va, sym, _p in specs if va is None]
    if unresolved:
        failures.append(f"prologue_specs() could not resolve va for: {', '.join(unresolved)}")
    empty = [f"{c}/{n}" for c, n, _i, _v, _s, pin in specs if not pin]
    if empty:
        failures.append(f"prologue_specs() decoded an empty pin for: {', '.join(empty)}")
    check("no PrologueSpec the parser drops", prologue_spec_gaps(), [])
    seams = seam_specs()
    if not seams:
        failures.append("seam_specs() found no MapSeam prologue in er-invasion-warp/map_seams.rs")
    if any(not pin for _c, _n, _i, _v, _s, pin in seams):
        failures.append("seam_specs() decoded an empty pin")

    # Every `Image` variant the generator declares has a role here, and every branch of the
    # dispatch is reachable on synthetic bytes.
    check("every declared image variant is modelled", unmodelled_image_variants(), [])
    pin = bytes.fromhex("4053")
    game = ("crate", "SIG", "EldenRing2720", BASE + 0x10, "SIG_VA", pin)
    check("a pin arms on its own bytes", classify_prologue(game, bytes(0x10) + pin + bytes(4))[0], "ARMS")
    check("a pin on other bytes is a bad pin", classify_prologue(game, bytes(0x20))[0], "BAD-PIN")
    check("a pin past the image end is a bad pin", classify_prologue(game, bytes(0x11))[0], "BAD-PIN")
    check("an ersc pin is a named skip",
          classify_prologue(("c", "S", "Ersc201", BASE, "V", pin), b"")[0], "SKIP")
    check("an unknown variant is refused",
          classify_prologue(("c", "S", "EldenRing1171", BASE, "V", pin), b"")[0], "UNKNOWN-IMAGE")
    check("an unresolved va is a named skip",
          classify_prologue(("c", "S", "EldenRing2720", None, "V", pin), b"")[0], "SKIP")

    for line in failures:
        print(f"  FAIL {line}")
    print(f"selftest: {'FAILED' if failures else 'ok'} ({len(failures)} failure(s))")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--section",
        choices=("all", "patterns", "needles", "prologues", "sync"),
        default="all",
    )
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument(
        "--require-images",
        action="store_true",
        help="fail instead of skipping when the 2.7.2.0 image is missing",
    )
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    bad = 0
    if args.section in ("all", "sync"):
        bad += check_sync()
        print()
    if args.section in ("all", "patterns"):
        bad += run_patterns(args.require_images)
        print()
    if args.section in ("all", "needles"):
        bad += run_needles(args.require_images)
        print()
    if args.section in ("all", "prologues"):
        bad += run_prologues(args.require_images)
        print()
    print(f"verdict: {'FAILED' if bad else 'ok'} ({bad} problem(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
