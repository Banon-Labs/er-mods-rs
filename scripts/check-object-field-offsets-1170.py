#!/usr/bin/env python3
"""Make ELDEN RING 1.16.2 -> 1.17 STRUCT-FIELD drift loud instead of silent.

THE FAILURE CLASS THIS EXISTS FOR
---------------------------------
The 1.17 migration has three ways to be wrong about an address and only two of them speak:

  * a stale DETOUR target      -> `er-hook` refuses it and logs `HOOK REFUSED`;
  * an unmapped CALL/data RVA  -> the resolver returns 0 and the caller says so;
  * a stale STRUCT FIELD OFFSET -> `*(this + 0xNN)` quietly returns the NEIGHBOURING field.

The third has no refusal, no fault and no log line. It returns a plausible value of the right
width, forever. This gate is the missing alarm for it.

WHAT IT MEASURES, AND WHY THIS METHOD
-------------------------------------
Not a displacement census. A census answers "which offsets does the image read off this object",
which cannot say WHICH FIELD lives at an offset -- every interior byte of a big nested member is
witnessed too -- and it cannot see a move at all when both the old and new offsets happen to be
read somewhere. It produces plausible-looking wrong answers and they feel like confirmation.

This gate instead ALIGNS ONE FUNCTION'S TWO BODIES (scripts/pair-object-field-drift.py). When the
instruction sequences agree except for memory displacements, the code did not change, so
instruction k in 1.16.2 and instruction k in 1.17 are the SAME access to the SAME field -- and a
displacement difference is that field moving, by exactly that much. Each row below names the
witness function pair that produced its number.

WHAT WAS MEASURED (2026-08-31), AND THE CORRECTION IT CARRIES
--------------------------------------------------------------
`CS::PlayerGameData` grew 8 bytes in NET SIZE (0xae8 -> 0xaf0), but its fields did NOT all move by
8, and the difference is exactly the kind that a mechanical "+8 above the insertion" fix gets
wrong. 1.17 inserted ONE four-byte slot at 0x960 (a new byte field plus padding, in front of what
was `damage_negation_physical`). The 0x118-byte stat sub-object that used to start at 0x960 now
starts at 0x964 and is otherwise BYTE-IDENTICAL (its own constructor aligns with zero moved
offsets), so it ends at 0xa7c instead of 0xa78; the pointer member that follows needs 8-byte
alignment, so it lands at 0xa80 rather than 0xa7c. Hence:

    [0x000, 0x960)   held        e.g. equipment 0x2b0, face_data 0x760, is_main_player 0x8f0
    [0x960, 0xa78)   +4          e.g. resistance_gauges 0x9c8 -> 0x9cc
    [0xa78, 0xae8)   +8          e.g. scadutree override 0xab4 -> 0xabc

What the new field at 0x960 IS was established independently, from the other end: 1.17 also adds
`CS::MoveMapStep::_UpdateHorseType` (commit "The insertion was benign", bd er-effects-rs-xci9),
which re-applies the mount after a map move and reads `PlayerGameData+0x960` to make that
idempotent. Two derivations that share no evidence -- a constructor alignment here, a new callee
read there -- land on the same byte.

`CS::PlayerIns` did NOT grow at all: 8 bytes were inserted in (0x398, 0x3a8] and 8 bytes REMOVED
in (0x560, 0x580], so the band between them shifts +8 while the object size is unchanged and both
ends hold. A "+8 above the insertion" rule applied here would have corrupted
`PLAYER_INS_SESSION_MANAGER_PLAYER_ENTRY_OFFSET` = 0x6b8, which is witnessed HELD twice.

WHAT THE GATE ASSERTS
---------------------
  1. IMAGE half -- every frozen witness row re-measures to the same pair, live, from the two
     de-Arxan'd images. A row that cannot be measured is a FAILURE, not a pass: nine "audits" in
     this repo have reported zero findings from a matcher that had gone blind.
  2. SOURCE half -- each repo constant that names a field of these two objects still holds the
     1.16.2 literal this gate verified, at the file and line where it lives.
  3. THE LATENT ONE, which is the reason to have a gate rather than a report. 44 sites compute
     `offset_of!(PlayerGameData, ...)` against the sibling `fromsoftware-rs` binding, which is a
     1.16.2 model. Every field referenced TODAY sits below 0x960, so nothing is wrong -- but the
     mechanism looks maximally trustworthy (the compiler computed it) and is one added field
     reference away from silently reading a neighbour. So: any `offset_of!(PlayerGameData, X)`
     whose field is not in the verified-and-below-the-boundary set fails the build.

USAGE
    python3 scripts/check-object-field-offsets-1170.py
    python3 scripts/check-object-field-offsets-1170.py --selftest   # prove it can go red
    ER_DEOBF_1162=... ER_DEOBF_1170=... python3 scripts/check-object-field-offsets-1170.py

The IMAGE half skips when the two images are absent (they are gitignored game-derived binaries);
the SOURCE half always runs, and `--selftest` REQUIRES the images so a green selftest can never
mean "the image half never ran".
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMAGE_BASE = 0x140000000
IMAGE_1162 = Path(os.environ.get("ER_DEOBF_1162", REPO / "eldenring-deobf.bin"))
IMAGE_1170 = Path(os.environ.get("ER_DEOBF_1170", REPO / "eldenring-deobf-1.17.bin"))
MATCHER = REPO / "scripts" / "pair-object-field-drift.py"

# Directories that are copies of this tree, not this tree.
EXCLUDED_DIRS = (".git", "target", "node_modules", ".worktrees", ".claude")

# --------------------------------------------------------------------------------------------
# THE MEASUREMENT. Each row: which object, which offset, what 1.17 did to it, and the WITNESS --
# a function whose two bodies align instruction-for-instruction, so the displacement pair is the
# same access to the same field in both builds. `bases` restricts the reading to memory operands
# on registers that provably hold `this` in that function.
#
# HELD rows are not decoration. They are the frozen negatives: a matcher that has become
# over-broad (reporting every offset as +8) fails them, and `--selftest` proves that by perturbing
# each of them in the other direction.
# --------------------------------------------------------------------------------------------
PGD_CTOR = dict(va16=0x14025D580, len16=1199, va17=0x14025D550, len17=1199, bases=("rbx", "rcx"))
PLAYER_INS_CTOR = dict(va16=0x14064FE40, len16=2143, va17=0x140650C90, len17=2143, bases=("rbx",))
PGD_COPY_CHR_NAME = dict(
    va16=0x1402610C0, len16=120, va17=0x1402610D0, len17=120, bases=("rcx", "rbx", "rdi", "rsi")
)

WITNESSES = (
    # ---- CS::PlayerGameData -----------------------------------------------------------------
    ("PlayerGameData", "equipment", 0x2B0, 0x2B0, PGD_CTOR, "constructor stores its own vtable at [this+0]"),
    ("PlayerGameData", "face_data", 0x760, 0x760, PGD_CTOR, "constructor"),
    ("PlayerGameData", "chr_name_string_a (0x8e8)", 0x8E8, 0x8E8, PGD_CTOR, "constructor"),
    ("PlayerGameData", "is_main_player", 0x8F0, 0x8F0, PGD_CTOR, "constructor"),
    # The autoload identity path. The character name lives in THREE PGD storages -- the raw
    # wchar_t[17] at 0x9c and two CSWordCheckedStringInternal* at 0x8e8 / 0x8f8 -- and CopyChrName
    # is the native that writes all three, so one aligned function witnesses the whole identity
    # surface the loading screen and the save-slot list read.
    ("PlayerGameData", "character_name (raw)", 0x9C, 0x9C, PGD_COPY_CHR_NAME, "CopyChrName"),
    ("PlayerGameData", "chr_name_string_b (0x8f8)", 0x8F8, 0x8F8, PGD_COPY_CHR_NAME, "CopyChrName"),
    ("PlayerGameData", "is_main_player (second witness)", 0x8F0, 0x8F0, PGD_COPY_CHR_NAME, "CopyChrName"),
    ("PlayerGameData", "old_mount_handle (last held)", 0x958, 0x958, PGD_CTOR, "constructor"),
    ("PlayerGameData", "stat sub-object start", 0x960, 0x964, PGD_CTOR, "constructor; 1.17 adds `mov byte [this+0x960],0` before it"),
    ("PlayerGameData", "menu_ref_special_effect_1", 0xA78, 0xA80, PGD_CTOR, "constructor"),
    (
        "PlayerGameData",
        "item_replenish_tracker",
        0x5E8,
        0x5E8,
        dict(va16=0x140786430, len16=179, va17=0x1407872B0, len17=179, bases=("rcx", "rbx", "rdi", "rsi")),
        "SetItemReplenishState, the function er-better-refills detours",
    ),
    (
        "PlayerGameData",
        "resistance_gauges",
        0x9C8,
        0x9CC,
        dict(va16=0x14025FA60, len16=10, va17=0x14025FA70, len17=10, bases=("rcx",)),
        "GetResistanceGauge leaf accessor -- independent of the constructor",
    ),
    (
        "PlayerGameData",
        "proc_status_timer_max",
        0xA38,
        0xA3C,
        dict(va16=0x14025FA10, len16=12, va17=0x14025FA20, len17=12, bases=("rcx",)),
        "GetProcStatusTimerMax leaf accessor",
    ),
    (
        "PlayerGameData",
        "scadutree_blessing_override",
        0xAB4,
        0xABC,
        dict(va16=0x14025F5F0, len16=24, va17=0x14025F5D0, len17=24, bases=("rcx",)),
        "GetScadutreeBlessing; the pair is also map-rvas-1162-to-1170.py's KNOWN_MAPPINGS control",
    ),
    (
        "PlayerGameData",
        "scadutree_blessing (held INSIDE a function that also moved)",
        0xFC,
        0xFC,
        dict(va16=0x14025F5F0, len16=24, va17=0x14025F5D0, len17=24, bases=("rcx",)),
        "GetScadutreeBlessing reads 0xfc and 0xab4 in the same 5 instructions; only one moved",
    ),
    # ---- CS::PlayerIns ----------------------------------------------------------------------
    ("PlayerIns", "held below the insertion", 0x368, 0x368, PLAYER_INS_CTOR, "constructor stores its own vtable at [this+0]"),
    ("PlayerIns", "held above the removal", 0x580, 0x580, PLAYER_INS_CTOR, "constructor"),
    ("PlayerIns", "session_manager_player_entry", 0x6B8, 0x6B8, PLAYER_INS_CTOR, "constructor"),
    (
        "PlayerIns",
        "session_manager_player_entry (second witness)",
        0x6B8,
        0x6B8,
        dict(va16=0x1406507A0, len16=913, va17=0x1406515F0, len17=913, bases=("rcx", "rbx", "rdi", "rsi")),
        "~PlayerIns",
    ),
    (
        "PlayerIns",
        "field in the shifted band (0x532)",
        0x532,
        0x53A,
        dict(va16=0x140653290, len16=600, va17=0x1406540E0, len17=600, bases=("rcx", "rdi")),
        "vtable slot 89 of CS::PlayerIns",
    ),
    (
        "PlayerIns",
        "field in the shifted band (0x538)",
        0x538,
        0x540,
        dict(va16=0x1403F09F0, len16=200, va17=0x1403F0C20, len17=200, bases=("rcx", "rdi")),
        "vtable slot 154 of CS::PlayerIns",
    ),
)

# The drift model the witnesses above establish, expressed as the SAFE region per object: an
# offset in one of these ranges is the same field in both builds. Anything outside needs a
# version-aware constant, which this workspace does not have for either object.
SAFE_REGIONS = {
    # Nothing at or above 0x960 held: 0x958 is the highest witnessed-held offset and 0x960 is the
    # lowest witnessed-moved one, from the SAME function, so the boundary is exact.
    "PlayerGameData": ((0x0, 0x960),),
    # 8 bytes inserted in (0x398,0x3a8] and 8 removed in (0x560,0x580]; the object size is
    # unchanged and both ends hold, so the hazard is the band between them, not the whole struct.
    "PlayerIns": ((0x0, 0x3A0), (0x568, 0x760)),
}

# --------------------------------------------------------------------------------------------
# SOURCE half. Each entry: the constant, the file that defines it, and the literal this gate
# verified against the images above.
# --------------------------------------------------------------------------------------------
PINNED_CONSTANTS = (
    ("PLAYER_GAME_DATA_EQUIP_GAME_DATA_OFFSET", "crates/er-better-refills/src/lib.rs", 0x2B0, "PlayerGameData"),
    ("PLAYER_GAME_DATA_ITEM_REPLENISH_TRACKER_OFFSET", "crates/er-better-refills/src/lib.rs", 0x5E8, "PlayerGameData"),
    ("PLAYER_GAME_DATA_EQUIP_OFFSET", "crates/er-build-import-runtime/src/grant.rs", 0x2B0, "PlayerGameData"),
    ("PLAYER_GAME_DATA_IS_MAIN_PLAYER_OFFSET", "crates/er-player-name-filter/src/lib.rs", 0x8F0, "PlayerGameData"),
    ("PLAYER_INS_SESSION_MANAGER_PLAYER_ENTRY_OFFSET", "crates/er-player-name-filter/src/lib.rs", 0x6B8, "PlayerIns"),
)

# Every `PlayerGameData` field this workspace reaches through `offset_of!`, with the offset the
# 1.16.2 binding computes for it. A field NOT in this table fails the gate: that is the whole
# point -- a new field reference must be measured against the images before it may be used, not
# trusted because the compiler was willing to compute it.
#
# The 25 marked `pinned` are additionally const-asserted in crates/er-game-base/src/pgd.rs. The 8
# marked `bracketed` are NOT const-asserted there and deliberately so: the 1.17 image never
# witnesses their offset, and each is only bracketed one or two slots wide by both-witnessed
# neighbours. A bracket is not a proof -- `CS::PlayerIns` is the counterexample, where a
# compensating insert/remove pair moved the interior of a bracket while both ends held. They are
# admitted here because every one of them is far below 0x960, which is the only claim this gate
# needs to make about them.
PGD_REFERENCED_FIELDS = {
    "current_hp": (0x10, "pinned"),
    "current_max_hp": (0x14, "pinned"),
    "base_max_hp": (0x18, "bracketed"),
    "current_fp": (0x1C, "pinned"),
    "current_max_fp": (0x20, "pinned"),
    "base_max_fp": (0x24, "bracketed"),
    "current_stamina": (0x2C, "pinned"),
    "current_max_stamina": (0x30, "pinned"),
    "base_max_stamina": (0x34, "bracketed"),
    "vigor": (0x3C, "pinned"),
    "mind": (0x40, "pinned"),
    "endurance": (0x44, "pinned"),
    "strength": (0x48, "pinned"),
    "dexterity": (0x4C, "pinned"),
    "intelligence": (0x50, "bracketed"),
    "faith": (0x54, "bracketed"),
    "arcane": (0x58, "bracketed"),
    "base_hero_point": (0x5C, "bracketed"),
    "level": (0x68, "pinned"),
    "rune_count": (0x6C, "pinned"),
    "rune_memory": (0x70, "pinned"),
    "chr_type": (0x98, "pinned"),
    "gender": (0xBE, "pinned"),
    "archetype": (0xBF, "pinned"),
    "voice_type": (0xC2, "pinned"),
    "starting_gift": (0xC3, "pinned"),
    "unlocked_talisman_slots": (0xC6, "pinned"),
    "matchmaking_spirit_ashes_level": (0xC7, "bracketed"),
    "matching_weapon_level": (0xE2, "pinned"),
    "max_hp_flask": (0x101, "pinned"),
    "max_fp_flask": (0x102, "pinned"),
    "equipment": (0x2B0, "pinned"),
    "face_data": (0x760, "pinned"),
}

OFFSET_OF_PGD = re.compile(r"offset_of!\s*\(\s*PlayerGameData\s*,\s*([A-Za-z0-9_]+)")
CONST_DEF = r"const\s+{name}\s*:\s*usize\s*=\s*(0x[0-9a-fA-F]+)\s*;"


_MATCHER = []


def load_matcher(fresh=False):
    """The single alignment implementation, shared with scripts/pair-object-field-drift.py.

    Cached: it holds the two 98 MB images in memory once loaded, and the selftest aligns the
    frozen rows hundreds of times.
    """
    if _MATCHER and not fresh:
        return _MATCHER[0]
    spec = importlib.util.spec_from_file_location("pair_object_field_drift", MATCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not fresh:
        _MATCHER.append(module)
    return module


def rust_files():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for name in files:
            if name.endswith(".rs"):
                yield Path(root) / name


def source_findings(read_text=None):
    """Constant pins and the `offset_of!` reference guard. Never touches the images."""
    read = read_text or (lambda p: p.read_text(encoding="utf-8", errors="replace"))
    findings = []
    # One pass over the tree: constant definitions and `offset_of!` references together. The
    # definition is looked for EVERYWHERE rather than only at its recorded home, because these
    # constants are actively being consolidated into `er-game-base::rva` and a gate that goes
    # quiet when a constant moves file is a gate that stops watching exactly when someone edits it.
    definitions = {name: [] for name, _rel, _expected, _obj in PINNED_CONSTANTS}
    patterns = {
        name: re.compile(CONST_DEF.format(name=re.escape(name)))
        for name, _rel, _expected, _obj in PINNED_CONSTANTS
    }
    referenced = {}
    for path in rust_files():
        text = read(path)
        where = str(path.relative_to(REPO))
        for name, pattern in patterns.items():
            for match in pattern.finditer(text):
                definitions[name].append((where, int(match.group(1), 16)))
        for match in OFFSET_OF_PGD.finditer(text):
            referenced.setdefault(match.group(1), set()).add(where)
    for name, rel, expected, _obj in PINNED_CONSTANTS:
        found = definitions[name]
        if not found:
            findings.append(
                f"{name}: no `const {name}: usize = 0x..;` anywhere in the tree "
                f"(last seen in {rel}); this gate can no longer watch it"
            )
        for where, value in found:
            if value != expected:
                findings.append(
                    f"{name}: {where} says {value:#x}, this gate verified {expected:#x} against "
                    "both images -- re-measure before changing it"
                )
    lo, hi = SAFE_REGIONS["PlayerGameData"][0]
    for field, where in sorted(referenced.items()):
        known = PGD_REFERENCED_FIELDS.get(field)
        if known is None:
            findings.append(
                f"offset_of!(PlayerGameData, {field}) at {sorted(where)[0]} is NOT in this gate's "
                "verified field table. The sibling binding is a 1.16.2 model; measure this field "
                "against both images and add it before reading it at runtime"
            )
            continue
        offset, _how = known
        if not lo <= offset < hi:
            findings.append(
                f"offset_of!(PlayerGameData, {field}) = {offset:#x} is at or above {hi:#x}, where "
                f"1.17 moved the fields. Used by {sorted(where)[0]}"
            )
    return findings, len(referenced)


def image_findings(matcher, capstone, md, rows=WITNESSES):
    """Re-measure every frozen row from the two images. Unmeasurable == failure."""
    findings, measured = [], 0
    for obj, label, old, new, witness, how in rows:
        pairs, _ins, _del, _rep = matcher.compare(
            capstone,
            md,
            witness["va16"] - IMAGE_BASE,
            witness["len16"],
            witness["va17"] - IMAGE_BASE,
            witness["len17"],
            witness["bases"],
            label,
            quiet=True,
        )
        seen = {o: n for o, n, _a, _b, _t in pairs}
        if old not in seen:
            findings.append(
                f"{obj}::{label}: witness ({how}) no longer reads {old:#x} at all -- the "
                "measurement went blind, which is not the same as a clean result"
            )
            continue
        measured += 1
        if seen[old] != new:
            findings.append(
                f"{obj}::{label}: {old:#x} -> {seen[old]:#x} measured, but this gate is frozen at "
                f"{old:#x} -> {new:#x}. Witness: {how}"
            )
    for obj, regions in SAFE_REGIONS.items():
        for _o, label, old, new, _w, _h in rows:
            if _o != obj or old == new:
                continue
            if any(lo <= old < hi for lo, hi in regions):
                findings.append(
                    f"{obj}::{label}: {old:#x} moved, yet {old:#x} is inside a region this gate "
                    "calls safe. SAFE_REGIONS and the witnesses disagree"
                )
    return findings, measured


def images_present():
    return IMAGE_1162.exists() and IMAGE_1170.exists()


def run(quiet=False, rows=WITNESSES, read_text=None):
    findings, referenced = source_findings(read_text=read_text)
    if not quiet:
        print(f"source: {len(PINNED_CONSTANTS)} constant pins, {referenced} PlayerGameData fields referenced")
    measured = 0
    if images_present():
        matcher = load_matcher()
        capstone, md = matcher._capstone()
        image, measured = image_findings(matcher, capstone, md, rows=rows)
        findings += image
        if not quiet:
            print(f"image:  {measured}/{len(rows)} frozen witness rows re-measured from both images")
    elif not quiet:
        print(f"image:  SKIPPED -- {IMAGE_1162.name} / {IMAGE_1170.name} absent (gitignored)")
    return findings, measured


def _selftest_mutants(matcher):
    """Perturbations that MUST make the gate red. A gate that survives them proves nothing."""
    cases = []
    for index, row in enumerate(WITNESSES):
        obj, label, old, new, witness, how = row
        # A MOVED row perturbed by another +4, and a HELD row perturbed to old+8. The second is
        # the frozen negative: a matcher that reported everything as moved would still pass the
        # first, and fails this one.
        bad = new + 4 if old != new else old + 8
        mutant = list(WITNESSES)
        mutant[index] = (obj, label, old, bad, witness, how)
        kind = "moved" if old != new else "HELD (frozen negative)"
        cases.append((f"{obj}::{label} [{kind}] expected {bad:#x}", tuple(mutant)))
    return cases


def selftest():
    if not images_present():
        print(
            "SELFTEST CANNOT RUN: this gate's whole claim is that it re-measures the images, so a "
            f"green selftest without {IMAGE_1162.name} / {IMAGE_1170.name} would be the exact "
            "vacuity it exists to prevent"
        )
        return 1
    failures = []
    findings, measured = run(quiet=True)
    if findings:
        failures.append(f"the unmutated tree is already red: {findings[0]}")
    if measured != len(WITNESSES):
        failures.append(f"only {measured}/{len(WITNESSES)} witness rows measured on a clean run")

    matcher = load_matcher()
    for name, mutant in _selftest_mutants(matcher):
        mutant_findings, _ = run(quiet=True, rows=mutant)
        if not mutant_findings:
            failures.append(f"mutant survived: {name}")

    # A lobotomised matcher must not read as clean.
    real_compare = matcher.compare
    try:
        blind = load_matcher(fresh=True)
        blind.compare = lambda *a, **k: ([], [], [], [])
        capstone, md = blind._capstone()
        blind_findings, blind_measured = image_findings(blind, capstone, md)
        if not blind_findings or blind_measured:
            failures.append("a matcher that measures nothing was not reported as a failure")
    finally:
        matcher.compare = real_compare

    # A source read that returns nothing must not read as clean either.
    blind_source, blind_referenced = source_findings(read_text=lambda _p: "")
    if not blind_source or blind_referenced:
        failures.append("a source half that reads empty files was not reported as a failure")

    # And a perturbed constant must be caught where it actually lives.
    name, rel, expected, _obj = PINNED_CONSTANTS[0]
    def swapped(path):
        text = path.read_text(encoding="utf-8", errors="replace")
        return text.replace(f"{expected:#x}", f"{expected + 8:#x}") if str(path).endswith(rel) else text
    perturbed, _ = source_findings(read_text=swapped)
    if not any(name in f for f in perturbed):
        failures.append(f"a changed literal for {name} was not caught")

    if failures:
        for line in failures:
            print(f"SELFTEST FAILED: {line}")
        return 1
    print(
        f"selftest ok: {len(WITNESSES)} witness rows re-measure clean, each of "
        f"{len(_selftest_mutants(matcher))} single-row perturbations goes red, and a blind matcher, "
        "an empty source read and a changed constant literal are all reported"
    )
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    findings, _measured = run()
    if findings:
        print(f"\n{len(findings)} FINDING(S):")
        for line in findings:
            print(f"  * {line}")
        return 1
    print("ok: no repo constant sits on a field 1.17 moved, and every witness still measures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
