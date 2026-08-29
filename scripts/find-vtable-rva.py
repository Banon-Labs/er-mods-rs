#!/usr/bin/env python3
"""Locate a C++ class's vtable in an ELDEN RING image, by RTTI, in BOTH builds at once.

WHY THIS EXISTS
---------------
The 1.16.2 -> 1.17 migration has a gate for CODE addresses (`er_game_base::game_build`, fed by
`docs/recon/rva-map-1162-to-1170.needed-verified.tsv`). It does not cover VTABLE and other `.rdata`
addresses, and those moved too. Measured 2026-08-29: `TITLE_OWNER_VTABLE_RVA = 0x2b63bb0`, hard
coded in three crates, is `CS::TitleStep` in 1.16.2 and is not a vtable at all in 1.17. The scans
that use it are read-only and fault-safe, so nothing crashes -- they silently find nothing, and the
features behind them are quietly dead. A silent wrong answer is worse than a refusal, which is why
this exists as a tool rather than a note.

HOW IT WORKS (MSVC x64 RTTI)
    vtable[-1]  -> RTTICompleteObjectLocator (absolute VA)
    COL + 0x0c  -> pTypeDescriptor, an image-relative RVA
    TD  + 0x10  -> the mangled name, NUL-terminated, e.g. ".?AVCSFadeImp@CS@@"

Both images are FLAT: file offset == RVA, VA = 0x140000000 + offset. So the scan is a single pass
over the file looking for qwords that point at a COL whose type descriptor carries the wanted name.

USAGE
    python3 scripts/find-vtable-rva.py CSFadeImp
    python3 scripts/find-vtable-rva.py TitleStep TitleTopDialog
    python3 scripts/find-vtable-rva.py --rva 0x2b63bb0      # inverse: what lives here?
    python3 scripts/find-vtable-rva.py --selftest
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

BASE = 0x140000000
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = {
    "1.16.2": os.environ.get("ER_DEOBF_IMAGE_1162", os.path.join(REPO, "eldenring-deobf.bin")),
    "1.17": os.environ.get("ER_DEOBF_IMAGE_1170", os.path.join(REPO, "eldenring-deobf-1.17.bin")),
}
# A type descriptor's name starts 0x10 bytes in, past the vfptr and the spare pointer.
TYPE_DESCRIPTOR_NAME_OFFSET = 0x10
# RTTICompleteObjectLocator: signature, offset, cdOffset, pTypeDescriptor, pClassDescriptor(, pSelf).
COL_TYPE_DESCRIPTOR_OFFSET = 0x0C


def load(path: str) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def class_name_at_vtable(image: bytes, vtable_rva: int) -> str | None:
    """The RTTI class name for a vtable at `vtable_rva`, or None if that is not a vtable."""
    if not 8 <= vtable_rva < len(image):
        return None
    col_va = struct.unpack_from("<Q", image, vtable_rva - 8)[0]
    if not BASE <= col_va < BASE + len(image):
        return None
    col = col_va - BASE
    if col + COL_TYPE_DESCRIPTOR_OFFSET + 4 > len(image):
        return None
    type_descriptor = struct.unpack_from("<I", image, col + COL_TYPE_DESCRIPTOR_OFFSET)[0]
    start = type_descriptor + TYPE_DESCRIPTOR_NAME_OFFSET
    if not 0 < start < len(image):
        return None
    end = image.find(b"\0", start, start + 256)
    if end < 0:
        return None
    name = image[start:end]
    return name.decode("ascii", "replace") if name.startswith(b".?A") else None


def find_vtables(image: bytes, wanted: str) -> list[int]:
    """Every vtable rva in the image whose RTTI name contains `wanted`.

    Driven from the TYPE DESCRIPTORS rather than by testing all ~12M qwords: find the descriptors
    whose name matches, then the COLs that reference them, then the vtables that reference those.
    Three narrow passes instead of one enormous one.
    """
    needle = wanted.encode("ascii", "replace")
    descriptors = []
    at = image.find(b".?A")
    while at >= 0:
        end = image.find(b"\0", at, at + 256)
        if end > 0 and needle in image[at:end]:
            descriptors.append(at - TYPE_DESCRIPTOR_NAME_OFFSET)
        at = image.find(b".?A", at + 1)
    if not descriptors:
        return []
    wanted_descriptors = set(descriptors)

    locators = set()
    for offset in range(0, len(image) - 4, 4):
        value = struct.unpack_from("<I", image, offset)[0]
        if value in wanted_descriptors and offset >= COL_TYPE_DESCRIPTOR_OFFSET:
            locators.add(BASE + offset - COL_TYPE_DESCRIPTOR_OFFSET)
    if not locators:
        return []

    found = []
    for offset in range(0, len(image) - 8, 8):
        if struct.unpack_from("<Q", image, offset)[0] in locators:
            found.append(offset + 8)
    return found


def selftest() -> int:
    """Two facts measured on 2026-08-29, one per image, plus the inverse lookup."""
    failures = []
    images = {name: load(path) for name, path in IMAGES.items()}
    for name, image in images.items():
        if image is None:
            print(f"selftest SKIP: {name} image not present at {IMAGES[name]}")
            return 0
    got = class_name_at_vtable(images["1.17"], 0x2B6D728)
    if got != ".?AVCSFadeImp@CS@@":
        failures.append(f"1.17 0x2b6d728: got {got!r}, want '.?AVCSFadeImp@CS@@'")
    got = class_name_at_vtable(images["1.16.2"], 0x2B63BB0)
    if got != ".?AVTitleStep@CS@@":
        failures.append(f"1.16.2 0x2b63bb0: got {got!r}, want '.?AVTitleStep@CS@@'")
    # The whole point: that rva is NOT the same vtable in 1.17.
    if class_name_at_vtable(images["1.17"], 0x2B63BB0) == ".?AVTitleStep@CS@@":
        failures.append("1.17 0x2b63bb0 still resolves to TitleStep -- the premise of this tool is wrong")
    for line in failures:
        print(f"selftest FAIL {line}")
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="class names (substring of the mangled RTTI name)")
    parser.add_argument("--rva", help="inverse lookup: name the class whose vtable is at this rva")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    images = {}
    for name, path in IMAGES.items():
        image = load(path)
        if image is None:
            print(f"missing image for {name}: {path}", file=sys.stderr)
            return 1
        images[name] = image

    if args.rva:
        rva = int(args.rva, 0) & 0xFFFFFFFF
        print(f"vtable rva 0x{rva:x}")
        for name, image in images.items():
            print(f"  {name:>6} -> {class_name_at_vtable(image, rva) or '(not a vtable)'}")
        return 0

    if not args.names:
        parser.error("give one or more class names, or --rva")
    for wanted in args.names:
        print(f"{wanted}:")
        for name, image in images.items():
            hits = find_vtables(image, wanted)
            shown = ", ".join(f"0x{h:x}" for h in hits[:8]) or "(none)"
            more = f" (+{len(hits) - 8} more)" if len(hits) > 8 else ""
            print(f"  {name:>6}  {shown}{more}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
