#!/usr/bin/env python3
"""Check the generated prologue masks, and re-run every prologue gate under masked comparison.

What this is for
----------------
Every detour in this workspace byte-checks its target's prologue before installing. Some of those
pins open with `mov rax, [rip+disp32]`, and a rip-relative displacement is the delta from the end
of the instruction to the global it names -- both ends move when the game is patched, so those four
bytes are guaranteed to re-encode. Measured on the 1.17 patch: `SAVE_REQUEST_RETRACT_B72_SIG`,
`..._B73_SIG` and `QUIT_PHASE_SETTLE_SIG` were the same instructions doing the same job at the right
addresses, and all three gates disarmed on nothing but that field. The features were silently off.

`build-support/prologue_build.rs` emits, beside every generated pin, a `<NAME>_MASK` in which
0xff = compare and 0x00 = ignore, and only a RIP-relative displacement ever gets 0x00. This script
is the offline gate on that:

  masks      Every emitted `<NAME>_MASK` is re-derived independently, with capstone, by
             `derive_rip_mask` below, and must agree byte for byte with what `iced-x86` emitted in
             the build script. Two decoders, one rule; a disagreement is a defect in one of them.
  prologues  Every pin re-checked against `eldenring-deobf-1.17.2.bin` at its own VA under the
             masked comparison, so the verdict is the one the running DLL would reach rather than
             a plain `==`. The pins are 2.7.2.0 bytes, so a pin that fails here is a bad pin.
  negative   The fail-closed proof: take a real masked pin, mutate an opcode byte, and require the
             masked comparison to still reject it. Also proves `+0xb72` and `+0xb73` still refuse
             each other, since a mask that swallowed the register-base displacement would let each
             accept the other function.

What it does not prove
----------------------
A pin that arms proves the pin accepts the bytes at that address. It does not prove the hook
installs, that the function still does the same job, or that the feature works. Nothing here runs
against a live game.

Usage
    python3 scripts/verify-prologue-masks.py                # all three sections
    python3 scripts/verify-prologue-masks.py --selftest     # no image, no build needed
    python3 scripts/verify-prologue-masks.py --section masks
    python3 scripts/verify-prologue-masks.py --require-images
    python3 scripts/verify-prologue-masks.py --require-generated

Exit 0 = every emitted mask matches the independent derivation, every negative control still
rejects, and every pin arms. Exit non-zero = a count of problems, each printed.

capstone is not installed system-wide on purpose; the script re-execs itself under
`uv run --with capstone` when the import fails.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import er_game_image  # noqa: E402 - repo-local, and the sys.path line above is what makes it work

BASE = er_game_image.BASE
COMPARED = 0xFF
IGNORED = 0x00


def _bootstrap_capstone() -> None:
    """Re-exec under `uv run --with capstone` when capstone is not importable."""
    try:
        import capstone  # noqa: F401

        return
    except ImportError:
        pass
    if os.environ.get("_ER_PROLOGUE_MASK_BOOTSTRAPPED"):
        print("capstone unavailable even under uv; install it or run with uv", file=sys.stderr)
        raise SystemExit(2)
    # `os.execvp`, not a subprocess: this is a re-exec of this script under uv, so there is no
    # parent left to supervise a child and nothing for a timeout to bound.
    os.environ["_ER_PROLOGUE_MASK_BOOTSTRAPPED"] = "1"
    os.execvp(
        "uv",
        ["uv", "run", "--with", "capstone", "python3", str(Path(__file__).resolve()), *sys.argv[1:]],
    )


def _load(name: str, filename: str):
    """Import a sibling script by path. These are `-`-named files, so a plain import cannot."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# `verify-aob-patterns.py` owns the spec enumeration. It is imported rather than re-implemented:
# this script is the masked companion to its `prologues` section, not a replacement for it.
AOB = _load("verify_aob_patterns", "verify-aob-patterns.py")


# --- the generated masks -----------------------------------------------------------------------
_GENERATED = re.compile(
    r"const (?P<name>[A-Z0-9_]+): (?:&\[u8\]|\[u8; \d+\]) = &?\[(?P<body>[^\]]*)\];",
    re.DOTALL,
)
_HEXBYTE = re.compile(r"0x([0-9a-fA-F]{2})")


def generated_constants() -> dict[str, bytes]:
    """`{name: bytes}` for every constant in every generated prologue file, newest build first.

    OUT_DIR accumulates one directory per build-script fingerprint and stale ones are never
    deleted, so a file that predates the mask work still sits there. Reading newest-first and
    keeping the first answer per name means the check describes the current build.
    """
    files = sorted(
        ROOT.glob("target/*/*/build/*/out/generated_*.rs"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    out: dict[str, bytes] = {}
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _GENERATED.finditer(text):
            name = match.group("name")
            if name in out:
                continue
            out[name] = bytes(int(b, 16) for b in _HEXBYTE.findall(match.group("body")))
    return out


def _disp_field(insn) -> tuple[int, int]:
    """`(offset, size)` of an instruction's displacement bytes, across capstone versions."""
    encoding = getattr(insn, "encoding", None)
    if encoding is not None:
        return encoding.disp_offset, encoding.disp_size
    return getattr(insn, "disp_offset", 0), getattr(insn, "disp_size", 0)


def derive_rip_mask(pin: bytes) -> bytes:
    """The independent derivation: capstone, masking only a RIP-relative displacement.

    Decodes the pin from its first byte (a pin is a window that starts at an instruction
    boundary), keeps decoding past a `ret`/`jmp` -- a pin may legitimately contain one, and the
    mask must cover the whole pin -- and ignores exactly the disp32 of any operand whose base is
    rip. Everything else, including register-base displacements and immediates, stays compared,
    because those are what tell `+0xb72` from `+0xb73`. A truncated tail capstone cannot decode
    stays compared, as the generator keeps it.
    """
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs, x86_const

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    keep = bytearray()
    for insn in md.disasm(bytes(pin), 0):
        if len(keep) >= len(pin):
            break
        field = bytearray(b"\x01" * insn.size)
        rip_relative = any(
            operand.type == x86_const.X86_OP_MEM and operand.mem.base == x86_const.X86_REG_RIP
            for operand in insn.operands
        )
        if rip_relative:
            at, size = _disp_field(insn)
            for index in range(at, min(at + size, len(field))):
                field[index] = 0
        keep += field
    keep = keep[: len(pin)]
    if len(keep) < len(pin):
        keep += b"\x01" * (len(pin) - len(keep))
    return bytes(COMPARED if byte else IGNORED for byte in keep)


def masked_equal(actual: bytes, expected: bytes, mask: bytes) -> bool:
    """The runtime comparison, mirrored: `er_game_base::prologue::matches_masked`."""
    if not expected or len(actual) < len(expected) or len(mask) != len(expected):
        return False
    if COMPARED not in mask:
        return False
    return all(
        keep != COMPARED or want == got
        for want, got, keep in zip(expected, actual, mask)
    )


def run_masks(require_generated: bool) -> int:
    constants = generated_constants()
    masks = {name[: -len("_MASK")]: value for name, value in constants.items() if name.endswith("_MASK")}
    if not masks:
        line = (
            "== masks: SKIPPED -- no generated `<NAME>_MASK` found under target/. Build a DLL "
            "first: cargo xwin build --release --target x86_64-pc-windows-msvc -p er-quickload"
        )
        print(line)
        return 1 if require_generated else 0
    print("== masks: each generated mask re-derived independently with capstone ==")
    print("   MATCH     iced-x86 (build script) and capstone (this script) agree byte for byte.")
    print("   DIFFERS   the two decoders disagree -- one of them is wrong, do not trust the pin.")
    problems = 0
    masked_specs = []
    for name in sorted(masks):
        emitted = masks[name]
        pin = constants.get(name)
        if pin is None:
            print(f"    ORPHAN   {name}_MASK has no matching pin constant")
            problems += 1
            continue
        if len(pin) != len(emitted):
            print(f"    DIFFERS  {name}: pin is {len(pin)} bytes, mask is {len(emitted)}")
            problems += 1
            continue
        stray = set(emitted) - {COMPARED, IGNORED}
        if stray:
            print(f"    DIFFERS  {name}: mask holds non-0xff/0x00 byte(s) {sorted(stray)}")
            problems += 1
            continue
        if emitted[0] != COMPARED:
            print(f"    DIFFERS  {name}: mask ignores the opening byte, which is the opcode")
            problems += 1
            continue
        derived = derive_rip_mask(pin)
        if derived != emitted:
            print(
                f"    DIFFERS  {name}: iced emitted {emitted.hex()}, capstone derives "
                f"{derived.hex()}"
            )
            problems += 1
            continue
        ignored = [index for index, byte in enumerate(emitted) if byte == IGNORED]
        if ignored:
            masked_specs.append((name, ignored))
    print(f"\n  {len(masks)} generated mask(s); {len(masked_specs)} ignore any byte at all:")
    for name, ignored in masked_specs:
        print(f"    {name}: ignores +{ignored[0]}..+{ignored[-1] + 1} (a RIP-relative disp32)")
    if not masked_specs:
        print("    (none -- every pin is a plain exact comparison)")
    return problems


# --- every gate, under the masked comparison ----------------------------------------------------
def classify_masked(spec, data, constants):
    """`(verdict, detail)` for one spec, compared the way the DLL compares it."""
    _crate, name, image_kind, va, va_sym, pin = spec
    # The generated mask when there is one; a hand-written `MapSeam` prologue has none and is
    # compared exactly, which is what `verify_seam` does. No build at all derives the mask the
    # build would have emitted, so the section runs in a checkout with no `target/`.
    mask = constants.get(f"{name}_MASK") or (
        bytes([COMPARED] * len(pin)) if va_sym == "inline" else derive_rip_mask(pin)
    )
    ignored = sum(1 for byte in mask if byte == IGNORED)
    note = f" [{ignored} byte(s) masked]" if ignored else ""
    if AOB.IMAGE_ROLE.get(image_kind) != "game":
        return "SKIP", f"{image_kind} module, not the game image"
    if va is None:
        return "SKIP", f"could not resolve {va_sym}"
    rva = va - BASE
    after = data[rva : rva + len(pin)]
    if len(after) != len(pin):
        return "DISARMS", f"{va:#x} is past the end of the image"
    if masked_equal(after, pin, mask):
        how = "" if after == pin else " (only because the masked operand is ignored)"
        return "ARMS", f"{va:#x}{note}{how}"
    differ = [i for i in range(len(pin)) if mask[i] == COMPARED and pin[i] != after[i]]
    return "DISARMS", (
        f"{va:#x}{note}: compared byte(s) {differ} differ; want {pin.hex()} got {after.hex()}"
    )


def run_prologues(require_images: bool) -> int:
    path = er_game_image.image_path()
    if path is None:
        print(f"== prologues: SKIPPED -- {er_game_image.missing_reason()}")
        return 1 if require_images else 0
    data = path.read_bytes()
    constants = generated_constants()
    specs = AOB.prologue_specs() + AOB.seam_specs()
    print("\n== prologues: every gate, compared the way the DLL compares it ==")
    print("   ARMS      the masked comparison passes at the pin's own 2.7.2.0 VA.")
    print("   DISARMS   it fails -- a byte the mask does not ignore differs. The pin is wrong.")
    print("   A pin that ARMS proves the pin accepts those bytes. Nothing here runs the game.")
    tally = {"ARMS": 0, "DISARMS": 0, "SKIP": 0}
    rows = []
    for spec in specs:
        verdict, detail = classify_masked(spec, data, constants)
        tally[verdict] += 1
        rows.append((spec[0], spec[1], verdict, detail))
    width = max((len(r[1]) for r in rows), default=0)
    last = None
    for crate, name, verdict, detail in rows:
        if crate != last:
            print(f"\n  {crate}")
            last = crate
        print(f"    {verdict:<8s} {name:<{width}s}  {detail}")
    print("\n  totals: " + "  ".join(f"{k}={v}" for k, v in tally.items()) + f"  (of {len(specs)} specs)")
    return tally["DISARMS"]


# --- the negative controls -----------------------------------------------------------------------
# Real bytes. The pins are the 2.7.2.0 bodies (`eldenring-deobf-1.17.2.bin` at `0x140679a40`,
# `0x140679a10` and `0x14067bc80`, the VAs the crates pin); the relocated bodies are the same
# functions in 1.16.2 (`eldenring-deobf.bin`), whose only difference is the rip-relative disp32
# -- the shape the next patch will produce.
B72_PIN = bytes.fromhex("488b05616f6f03c680720b000000c3")
B72_RELOCATED = bytes.fromhex("488b05d1116f03c680720b000000c3")
B73_PIN = bytes.fromhex("488b05916f6f03c680730b000000c3")
SETTLE_PIN = bytes.fromhex("488b05214d6f0383b8c40b000002750a")
SETTLE_RELOCATED = bytes.fromhex("488b0591ef6e0383b8c40b000002750a")


def run_negative() -> int:
    print("\n== negative controls: what the masked comparison must still refuse ==")
    problems = 0
    cases = []
    for label, pin in (("B72", B72_PIN), ("SETTLE", SETTLE_PIN)):
        mask = derive_rip_mask(pin)
        ignored = [i for i, byte in enumerate(mask) if byte == IGNORED]
        cases.append((f"{label}: mask derived from the pin ignores +{ignored[0]}..+{ignored[-1] + 1}", True))
        if ignored != [3, 4, 5, 6]:
            print(f"    FAIL  {label}: expected the disp32 at +3..+7 to be the only masked run, got {ignored}")
            problems += 1

    mask72 = derive_rip_mask(B72_PIN)
    checks = [
        ("the relocated B72 body is accepted", masked_equal(B72_RELOCATED, B72_PIN, mask72)),
        (
            "the relocated settle body is accepted",
            masked_equal(SETTLE_RELOCATED, SETTLE_PIN, derive_rip_mask(SETTLE_PIN)),
        ),
        ("+0xb73's body is still refused by the +0xb72 pin", not masked_equal(B73_PIN, B72_PIN, mask72)),
    ]
    # Mutate one byte at a time and require refusal everywhere the mask does not ignore.
    for position in range(len(B72_PIN)):
        mutated = bytearray(B72_RELOCATED)
        mutated[position] ^= 0x01
        refused = not masked_equal(bytes(mutated), B72_PIN, mask72)
        expected_refused = mask72[position] == COMPARED
        checks.append(
            (
                f"byte +{position} flipped -> {'refused' if expected_refused else 'ignored'}",
                refused == expected_refused,
            )
        )
    # The headline control, stated on its own: an opcode byte.
    opcode_mutant = bytearray(B72_RELOCATED)
    opcode_mutant[1] = 0x8D  # `mov rax,[rip+..]` -> `lea rax,[rip+..]`
    checks.append(
        (
            "opcode 8b -> 8d (mov becomes lea) is refused even though only the operand is masked",
            not masked_equal(bytes(opcode_mutant), B72_PIN, mask72),
        )
    )
    for label, ok in cases + checks:
        print(f"    {'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            problems += 1
    return problems


def selftest() -> int:
    """The parts that need no game image and no build: the rule, the dispatch and the controls."""
    problems = run_negative()

    def expect(label, ok):
        nonlocal problems
        print(f"    {'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            problems += 1

    expect("a prologue with no RIP operand derives an all-compared mask",
           derive_rip_mask(bytes.fromhex("4055565748")) == bytes([COMPARED] * 5))
    expect("an empty pin is refused rather than matched", not masked_equal(b"", b"", b""))
    expect("an all-ignored mask is refused rather than matching a zeroed block",
           not masked_equal(bytes(15), B72_PIN, bytes(15)))
    # The per-spec dispatch, on synthetic image bytes.
    data = bytes(0x10) + B72_RELOCATED + bytes(8)
    spec = ("crate", "B72", "EldenRing2720", BASE + 0x10, "B72_VA", B72_PIN)
    expect("a relocated body arms under the derived mask", classify_masked(spec, data, {})[0] == "ARMS")
    expect("other bytes disarm", classify_masked(spec, bytes(0x40), {})[0] == "DISARMS")
    seam = ("crate", "SEAM", "EldenRing2720", BASE + 0x10, "inline", B72_PIN)
    expect("a hand-written seam is compared exactly, so the relocated body disarms",
           classify_masked(seam, data, {})[0] == "DISARMS")
    expect("an ersc pin is a named skip",
           classify_masked(("c", "S", "Ersc202", BASE, "V", B72_PIN), data, {})[0] == "SKIP")
    print(f"\nselftest: {problems} problem(s)")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", choices=["masks", "prologues", "negative"], action="append")
    parser.add_argument("--selftest", action="store_true", help=selftest.__doc__)
    parser.add_argument("--require-images", action="store_true")
    parser.add_argument("--require-generated", action="store_true")
    arguments = parser.parse_args()
    if arguments.selftest:
        return selftest()
    sections = arguments.section or ["masks", "prologues", "negative"]
    problems = 0
    if "masks" in sections:
        problems += run_masks(arguments.require_generated)
    if "prologues" in sections:
        problems += run_prologues(arguments.require_images)
    if "negative" in sections:
        problems += run_negative()
    print(f"\nverdict: {'ok' if problems == 0 else 'FAILED'} ({problems} problem(s))")
    return 1 if problems else 0


if __name__ == "__main__":
    _bootstrap_capstone()
    raise SystemExit(main())
