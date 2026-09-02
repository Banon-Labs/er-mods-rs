#!/usr/bin/env python3
"""Find the Seamless Co-op entry points this repo pins, in whatever `ersc.dll` is installed.

WHY THIS EXISTS
---------------
`crates/er-invasion-warp` reads and hooks four functions inside `ersc.dll` by
`module base + RVA`. Those RVAs were measured against **Seamless Co-op v1.9.9**. `ersc.dll` is
third-party: the user installs and updates it whenever they like, and on 2026-09-02 **v2.0.0**
replaced v1.9.9 and moved everything. The pinned VA `0x180022d30` -- `show()` in v1.9.9 -- now
reads `ff ff ff 7f ...`, a float bit pattern in the middle of unrelated data.

That is not a repo defect and no longer breaks the build (see the "Whose file is it?" section of
`build-support/prologue_build.rs`). It does mean the runtime filter DISARMS, because its prologue
gate correctly refuses to call an address it cannot recognise. This tool is how you find out where
things went before re-pinning them.

WHAT A RESULT MEANS -- read this before pinning anything it prints
------------------------------------------------------------------
A match here is a CANDIDATE, never an identification. Two lessons from the v1.9.9 -> v2.0.0 move,
both measured:

* The eight callee-saved pushes that open `show()` appear **1248 times** in `ersc.dll`. A prologue
  is a code SHAPE. Only with the frame size appended does it become unique.
* `BUILD_LOBBY_KEY_PROLOGUE`'s full 19 bytes match exactly ONE place in v2.0.0 -- and it is the
  WRONG function. Mapping the v1.9.9 function's BODY by masked content lands somewhere else
  entirely. A unique byte match is evidence, not proof; read the function.

And the addresses are not the whole job. v2.0.0 also moved the session object's fields and changed
the option-action codes, both of which `local_invasion_filter.rs` hard-codes:

    field / code                     v1.9.9        v2.0.0
    session state                    S+0x110       S+0x150
    guard                            S+0x10c       S+0x14c
    sub-object the actions lea       S+0xc0        S+0x100
    "invade" action writes           0xd           (no action writes 0xd)
    "cancel" action writes           0x22          0x23

So a correct v2.0.0 address used with v1.9.9 field offsets would read and write the wrong things
in a live multiplayer session. Re-pinning is a reverse-engineering job, not a find-and-replace.

USAGE
    uv run --with capstone python3 scripts/locate-ersc-entry-points.py
    uv run --with capstone python3 scripts/locate-ersc-entry-points.py --reference <v1.9.9 ersc.dll>
    python3 scripts/locate-ersc-entry-points.py --selftest

`capstone` is only needed for the `--reference` body mapping, which is the strong evidence; the
prologue search runs without it. There is no system pip -- use uv, as AGENTS.md says.

ENV
    ER_ERSC_DLL            the installed ersc.dll to inspect
    ER_ERSC_DLL_REFERENCE  a copy of the build the pins were measured against
    ME3_STEAM_DIR          Steam root, searched before the two default ones
"""

import argparse
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Single source of truth for the pinned addresses and bytes. Parsed rather than copied: a second
# copy of an RVA in a Python file is how the two drift and the tool starts lying.
BUILD_RS = os.path.join(ROOT, "crates", "er-invasion-warp", "build.rs")
ERSC_BASE = 0x180000000
RELATIVE_INSTALL = "steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll"
IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_WRITE = 0x80000000
# Same cap, and the same reason, as MAX_CONTENT_MATCHES in build-support/prologue_build.rs.
MAX_MATCHES = 8


# ---------------------------------------------------------------------------------------------
# PE
# ---------------------------------------------------------------------------------------------


class Pe:
    """The little of PE32+ this needs: sections, RVA->offset, and the real code ranges."""

    def __init__(self, image):
        self.image = image
        self.sections = []
        pe = struct.unpack_from("<I", image, 0x3C)[0]
        if image[pe : pe + 4] != b"PE\0\0":
            raise ValueError("not a PE image")
        count = struct.unpack_from("<H", image, pe + 6)[0]
        table = pe + 24 + struct.unpack_from("<H", image, pe + 20)[0]
        for index in range(count):
            entry = table + 40 * index
            name = image[entry : entry + 8].rstrip(b"\0").decode("latin1")
            vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", image, entry + 8)
            chars = struct.unpack_from("<I", image, entry + 36)[0]
            self.sections.append((name, vsize, vaddr, rsize, rptr, chars))

    def rva_to_offset(self, rva):
        for _, vsize, vaddr, rsize, rptr, _ in self.sections:
            if vaddr <= rva < vaddr + max(vsize, rsize):
                return rptr + (rva - vaddr)
        return None

    def bytes_at_va(self, va, length):
        offset = self.rva_to_offset(va - ERSC_BASE)
        if offset is None:
            return None
        window = self.image[offset : offset + length]
        return window if len(window) == length else None

    def code_ranges(self):
        """`(file_offset, length, rva_of_that_offset)` per section holding real compiler-emitted code.

        Executable AND NOT writable. `ersc.dll` keeps most of itself in an Oreans WinLicense VM
        section (`.themida` in v1.9.9, renamed `ERSC` in v2.0.0) that is 11 MB of ciphertext and
        is marked writable; scanning it manufactures coincidental hits. Selecting by section
        characteristics rather than by the name `.text` keeps the rule true if the next build
        renames things again -- which this one already did once.
        """
        ranges = []
        for _, _, vaddr, rsize, rptr, chars in self.sections:
            if chars & IMAGE_SCN_MEM_EXECUTE and not chars & IMAGE_SCN_MEM_WRITE:
                if rptr + rsize <= len(self.image):
                    ranges.append((rptr, rsize, vaddr))
        return ranges

    def find(self, pattern, mask=None):
        """Every VA in real code where `pattern` matches under `mask` (`None` = exact)."""
        mask = mask if mask is not None else b"\x01" * len(pattern)
        anchor = pattern[0] if mask[0] else None
        hits = []
        for start, length, rva in self.code_ranges():
            at = start
            end = start + length - len(pattern)
            while at <= end and len(hits) < MAX_MATCHES:
                if anchor is None or self.image[at] == anchor:
                    window = self.image[at : at + len(pattern)]
                    if all(not k or window[i] == pattern[i] for i, k in enumerate(mask)):
                        hits.append(ERSC_BASE + rva + (at - start))
                at += 1
        return hits

    def describe(self):
        pe = struct.unpack_from("<I", self.image, 0x3C)[0]
        stamp = struct.unpack_from("<I", self.image, pe + 8)[0]
        version = "unknown"
        for match in re.finditer(rb"S\0e\0a\0m\0l\0e\0s\0s\0", self.image):
            text = self.image[match.start() : match.start() + 120].decode("utf-16le", "replace")
            version = text.split("\x00")[0]
            break
        return f"{len(self.image)} bytes, PE timestamp {stamp}, {version!r}"


# ---------------------------------------------------------------------------------------------
# The pins, read out of build.rs
# ---------------------------------------------------------------------------------------------


def parse_pins(source):
    """`[(name, va, pin_bytes)]` for every `Image::Ersc` spec in er-invasion-warp's build.rs."""
    consts = {
        key: int(value, 16)
        for key, value in re.findall(r"const\s+(\w+_VA)\s*:\s*u64\s*=\s*(0x[0-9a-fA-F_]+)", source)
    }
    byte_consts = {
        key: parse_byte_array(body)
        for key, body in re.findall(
            r"const\s+(\w+)\s*:\s*&\[u8\]\s*=\s*&\[(.*?)\];", source, re.S
        )
    }
    pins = []
    for block in re.findall(r"PrologueSpec\s*\{(.*?)\n\s*\},", source, re.S):
        if "Image::Ersc" not in block:
            continue
        name = re.search(r'name:\s*"(\w+)"', block)
        va = re.search(r"va:\s*(\w+)", block)
        pin = re.search(r"pin:\s*(?:&\[(.*?)\]|(\w+))\s*,?\s*$", block, re.S | re.M)
        if not (name and va and pin):
            continue
        raw = parse_byte_array(pin.group(1)) if pin.group(1) else byte_consts.get(pin.group(2), b"")
        pins.append((name.group(1), consts.get(va.group(1)), raw))
    return pins


def parse_byte_array(body):
    return bytes(int(token, 16) for token in re.findall(r"0x([0-9a-fA-F]{2})", body))


# ---------------------------------------------------------------------------------------------
# Masked body mapping (the strong evidence; needs capstone)
# ---------------------------------------------------------------------------------------------

# Longest first. A long signature is stronger, but it reaches past a short function into whatever
# follows it, so a ladder recovers the ones a 64-byte window overshoots.
SIGNATURE_LADDER = (64, 48, 40, 32, 24)


def masked_pattern(image, offset, want):
    """`(pattern, mask)` from `offset` with every version-fragile operand byte wildcarded.

    Same three classes as `scripts/map-rvas-1162-to-1170.py`: RIP-relative displacements, memory
    displacements on a register base (struct fields move between builds -- v2.0.0 moved the ERSC
    session state from `+0x110` to `+0x150`), and immediates. What survives is opcode shape and
    register allocation, which is what identifies a function across a recompile.
    """
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    pattern, mask, consumed = bytearray(), bytearray(), 0
    for insn in md.disasm(bytes(image[offset : offset + want * 3]), 0):
        if consumed >= want:
            break
        raw = bytearray(insn.bytes)
        keep = bytearray(b"\x01" * len(raw))
        encoding = getattr(insn, "encoding", None)
        if encoding is not None:
            for at, size in (
                (encoding.disp_offset, encoding.disp_size),
                (encoding.imm_offset, encoding.imm_size),
            ):
                for index in range(at, min(at + size, len(keep))):
                    keep[index] = 0
        pattern += raw
        mask += keep
        consumed += len(raw)
    return (bytes(pattern), bytes(mask)) if pattern else (None, None)


def map_by_body(reference, installed, va):
    """Map one pinned VA from the reference build onto the installed one. `(hits, note)`."""
    offset = reference.rva_to_offset(va - ERSC_BASE)
    if offset is None:
        return [], "VA is outside every section of the reference"
    for length in SIGNATURE_LADDER:
        pattern, mask = masked_pattern(reference.image, offset, length)
        if pattern is None:
            return [], "reference bytes did not decode"
        hits = installed.find(pattern, mask)
        if hits:
            return hits, f"{len(pattern)}B masked body signature"
    return [], "no match at any signature length"


# ---------------------------------------------------------------------------------------------
# Locating the files
# ---------------------------------------------------------------------------------------------


def steam_roots():
    roots = []
    if os.environ.get("ME3_STEAM_DIR"):
        roots.append(os.environ["ME3_STEAM_DIR"])
    home = os.path.expanduser("~")
    roots.append(os.path.join(home, ".local/share/Steam"))
    roots.append(os.path.join(home, ".steam/steam"))
    return roots


def find_installed(explicit):
    """The same resolution order the build script uses, so both agree on which file is 'the' DLL."""
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    if os.environ.get("ER_ERSC_DLL"):
        path = os.environ["ER_ERSC_DLL"]
        return path if os.path.isfile(path) else None
    for root in steam_roots():
        candidate = os.path.join(root, RELATIVE_INSTALL)
        if os.path.isfile(candidate):
            return candidate
    return None


def find_reference(explicit, installed, pins):
    """A copy of the build the pins describe, VALIDATED by the pins themselves.

    `_SeamlessCoop/` beside the install is where the Seamless launcher leaves the previous
    version, which makes it the one bounded fallback worth having. It is only accepted if every
    pinned VA in it actually holds its pinned bytes -- otherwise it is just another build, and
    mapping from it would produce confident nonsense.
    """
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("ER_ERSC_DLL_REFERENCE"):
        candidates.append(os.environ["ER_ERSC_DLL_REFERENCE"])
    if installed:
        game = os.path.dirname(os.path.dirname(installed))
        candidates.append(os.path.join(game, "_SeamlessCoop", "ersc.dll"))
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as handle:
                pe = Pe(handle.read())
        except (ValueError, struct.error, OSError):
            continue
        if all(pin and pe.bytes_at_va(va, len(pin)) == pin for _, va, pin in pins):
            return path, pe
    return None, None


# ---------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------


def report(installed_path, installed, reference_path, reference, pins):
    print(f"installed:  {installed_path}\n            {installed.describe()}")
    if reference_path:
        print(f"reference:  {reference_path}\n            {reference.describe()}")
    else:
        print(
            "reference:  none -- pass --reference <a copy of the build the pins were measured\n"
            "            against> for body mapping, which is the strong evidence. Without it\n"
            "            only the prologue search below runs, and a prologue is a shape."
        )
    print()
    for name, va, pin in pins:
        print(f"{name} (pinned 0x{va:x}, {len(pin)} bytes)")
        actual = installed.bytes_at_va(va, len(pin))
        if actual is None:
            print("  pinned VA falls outside every section of the installed build")
        elif actual == pin:
            print("  PIN HOLDS -- this is the build these constants were measured against")
            continue
        else:
            print(f"  pin does not hold; that address holds {actual.hex(' ')}")
        hits = installed.find(pin)
        print(f"  by prologue: {format_hits(hits)}")
        if reference is not None:
            try:
                mapped, note = map_by_body(reference, installed, va)
            except ImportError:
                # Degrade rather than die: the prologue search above is still useful, and the
                # obvious thing to type is `python3 scripts/...`, which has no capstone.
                print(
                    "  by body: capstone not importable -- re-run as"
                    " `uv run --with capstone python3 scripts/locate-ersc-entry-points.py`"
                    " for the body mapping, which is the strong evidence"
                )
            else:
                print(f"  by body ({note}): {format_hits(mapped)}")
    print(
        "\nEvery address above is a CANDIDATE. Read the function before pinning it, and re-check\n"
        "the session field offsets and action codes too -- see this file's header for the ones\n"
        "v2.0.0 moved."
    )


def format_hits(hits):
    if not hits:
        return "no match"
    text = ", ".join(f"0x{va:x}" for va in hits)
    return text + (" (capped)" if len(hits) >= MAX_MATCHES else "")


# ---------------------------------------------------------------------------------------------
# Selftest -- no game files, so it runs anywhere
# ---------------------------------------------------------------------------------------------


def synth_pe(payload, decoy):
    """A minimal PE32+ with one `.text` (exec, not writable) and one packer-style `VM` section
    (exec AND writable). `payload` goes in both, so a locator that does not exclude writable
    executable sections reports two hits where it must report one."""
    text_raw, vm_raw = 0x400, 0x600
    text_rva, vm_rva = 0x1000, 0x2000
    image = bytearray(0x1000)
    struct.pack_into("<I", image, 0x3C, 0x80)
    pe = 0x80
    image[pe : pe + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", image, pe + 4, 0x8664, 2, 0, 0, 0, 0xF0, 0x2022)
    struct.pack_into("<H", image, pe + 24, 0x20B)
    table = pe + 24 + 0xF0
    for index, (name, rva, ptr, chars) in enumerate(
        [
            (b".text", text_rva, text_raw, IMAGE_SCN_MEM_EXECUTE),
            (b"VM", vm_rva, vm_raw, IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_MEM_WRITE),
        ]
    ):
        entry = table + 40 * index
        image[entry : entry + 8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", image, entry + 8, 0x200, rva, 0x200, ptr)
        struct.pack_into("<I", image, entry + 36, chars)
    image[text_raw : text_raw + len(payload)] = payload
    image[vm_raw : vm_raw + len(decoy)] = decoy
    return bytes(image), ERSC_BASE + text_rva


def selftest():
    failures = []

    payload = bytes([0x55, 0x41, 0x57, 0x48, 0x81, 0xEC, 0x88, 0x01, 0x00, 0x00, 0xC3])
    image, payload_va = synth_pe(payload, payload)
    pe = Pe(image)

    hits = pe.find(payload)
    if hits != [payload_va]:
        failures.append(
            f"locator returned {[hex(h) for h in hits]}, expected only 0x{payload_va:x} -- the "
            "writable executable (packer VM) section was not excluded"
        )

    if pe.bytes_at_va(payload_va, len(payload)) != payload:
        failures.append("bytes_at_va did not read back the payload it was given")

    mask = b"\x01" * 3 + b"\x00" * (len(payload) - 3)
    if len(pe.find(payload[:3] + b"\xff" * (len(payload) - 3), mask)) != 1:
        failures.append("masked search did not honour its mask")

    try:
        mapped, note = map_by_body(pe, pe, payload_va)
    except ImportError:
        print(
            "selftest: capstone absent, body mapping not exercised "
            "(run under `uv run --with capstone`)"
        )
    else:
        if mapped != [payload_va]:
            failures.append(f"self-mapping gave {[hex(h) for h in mapped]} ({note}), expected one")

    with open(BUILD_RS, encoding="utf-8") as handle:
        pins = parse_pins(handle.read())
    # `< 4` rather than `!= 4`: a broken regex parses too FEW, and a regex that over-matches into
    # the game-image specs is caught by the address-range check below instead. Pinning the exact
    # count would just break the day someone legitimately adds a fifth ERSC entry point.
    if len(pins) < 4:
        failures.append(f"parsed {len(pins)} ERSC pins out of {BUILD_RS}, expected at least 4")
    for name, va, pin in pins:
        if va is None or not (ERSC_BASE <= va < ERSC_BASE + (1 << 32)):
            failures.append(f"{name}: parsed VA {va!r} is not an ersc.dll address")
        if len(pin) < 8:
            failures.append(f"{name}: parsed pin is {len(pin)} bytes, too short to identify a shape")

    for failure in failures:
        print(f"SELFTEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"selftest passed ({len(pins)} pins parsed from {os.path.relpath(BUILD_RS, ROOT)})")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--installed", help="the ersc.dll to inspect (default: the Steam install)")
    parser.add_argument("--reference", help="a copy of the build the pinned RVAs were measured on")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    pins = parse_pins(open(BUILD_RS, encoding="utf-8").read())
    if not pins:
        print(f"no Image::Ersc prologue specs found in {BUILD_RS}", file=sys.stderr)
        return 2

    installed_path = find_installed(args.installed)
    if installed_path is None:
        print(
            "no ersc.dll found. Seamless Co-op is optional, so this is not an error -- the\n"
            "invasion filter simply never arms. Set ER_ERSC_DLL or pass --installed to inspect\n"
            "a copy somewhere else.",
            file=sys.stderr,
        )
        return 2
    try:
        installed = Pe(open(installed_path, "rb").read())
    except (ValueError, struct.error) as error:
        print(f"{installed_path}: not a readable PE image ({error})", file=sys.stderr)
        return 2

    reference_path, reference = find_reference(args.reference, installed_path, pins)
    if reference is None and args.reference:
        print(
            f"{args.reference}: rejected as a reference -- the pinned VAs do not hold the pinned\n"
            "bytes in it, so it is not the build these constants were measured against.",
            file=sys.stderr,
        )
    report(installed_path, installed, reference_path, reference, pins)
    return 0


if __name__ == "__main__":
    sys.exit(main())
