#!/usr/bin/env python3
"""Read the stored key-binding table out of an Elden Ring save container.

# The question this answers in one command

When a button stops working, the first fork is whether the binding is dead on disk or only in
memory, and those want completely different investigations. On 2026-09-19 that fork cost an
afternoon: `CSPcKeyConfig+0x440` row `0x0f` read `-1` in a live process, the only function in the
game that writes that table turned out to be a bulk copy from a serialized stream
(`FUN_14023f220`, paired with the serializer `FUN_14023f280`), and the obvious conclusion was that
the save carried the damage. It did not. Reading the file settled it, and reading the file is what
this script does.

# Where the table is, and why it is not at a fixed offset

The save is a `BND4` container: a `0x40`-byte header, then `0x20`-byte entry headers from `0x40`,
then the data. Ten character slots of `0x280010` each are followed by the system block, which is
where the bindings live -- the same 26 rows of five `int32` the serializer writes, in the same
order, uncompressed and unencrypted in this region.

Its offset inside that block is not fixed: the stream carries variable-length records ahead of it.
So the table is searched for rather than assumed, by looking for the shape only it has -- 26 rows at a
`0x14` stride whose known action rows still hold their default pad codes. The row under suspicion
is deliberately not part of the signature: requiring `0x0f` to read `2003` would make the script
fail to find the table in exactly the case it exists for.

    row 0x0d  switch magic            pad 2000
    row 0x0e  switch item             pad 2001
    row 0x0f  switch right armament   pad 2003   <- never required, this is the answer
    row 0x10  switch left armament    pad 2002

Read-only. The container is opened for reading and nothing is written anywhere.

    python3 scripts/er-save-keyconfig.py                   # every save under the default directory
    python3 scripts/er-save-keyconfig.py --save <path>     # one container
    python3 scripts/er-save-keyconfig.py --selftest
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import os
import struct
import sys
from pathlib import Path

# The serializer writes 26 rows of five int32 -- pad, keyboard, keyboard modifier, mouse, mouse
# modifier -- which is the `lVar11 = 0x1a` loop count and the `puVar10 + 5` stride in both
# `FUN_14023f220` and `FUN_14023f280` on the 1.17 image.
ROW_COUNT = 0x1a
ROW_STRIDE = 0x14
FIELDS = ("pad", "keyboard", "keyboard_modifier", "mouse", "mouse_modifier")

# A row with no button on it. The symptom being chased is this value in `pad`.
UNBOUND = -1

# Action ids, from the quick-slot handler `FUN_1407756b0`.
ACTIONS = {
    0x0D: "switch magic (d-pad up)",
    0x0E: "switch item (d-pad down)",
    0x0F: "switch right armament (d-pad right)",
    0x10: "switch left armament (d-pad left)",
}

# The signature used to locate the table. Deliberately excludes 0x0f.
SIGNATURE = {0x0D: 2000, 0x0E: 2001, 0x10: 2002}

# The row this exists to report, and what the game's own default table holds for it.
WATCHED_ACTION = 0x0F
WATCHED_DEFAULT_PAD = 2003

DEFAULT_SAVE_DIR = (
    Path.home()
    / ".local/share/Steam/steamapps/compatdata/1245620/pfx/drive_c/users/steamuser"
    / "AppData/Roaming/EldenRing"
)
SAVE_SUFFIXES = (".sl2", ".co2")


def find_table(data: bytes) -> int | None:
    """File offset of row 0, or None.

    Anchored on the first signature row so the scan is a `bytes.find` rather than a walk over
    every offset in a 29 MB file, then confirmed against the other rows at their own strides.
    """
    anchor_action = min(SIGNATURE)
    anchor = struct.pack("<i", SIGNATURE[anchor_action])
    at = data.find(anchor)
    while at != -1:
        base = at - anchor_action * ROW_STRIDE
        if base >= 0 and base + ROW_COUNT * ROW_STRIDE <= len(data):
            if all(
                struct.unpack_from("<i", data, base + action * ROW_STRIDE)[0] == pad
                for action, pad in SIGNATURE.items()
            ):
                return base
        at = data.find(anchor, at + 1)
    return None


def read_rows(data: bytes, base: int) -> list[tuple[int, ...]]:
    return [
        struct.unpack_from("<5i", data, base + row * ROW_STRIDE) for row in range(ROW_COUNT)
    ]


def report(path: Path) -> bool:
    """Print one container's table. True when the watched row holds a pad button."""
    data = path.read_bytes()
    stamp = _datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(sep=" ")[:19]
    base = find_table(data)
    if base is None:
        print(f"{path.name}  written {stamp}")
        print("  no binding table found -- the signature rows do not hold their default pad codes,")
        print("  so either these actions were rebound or this is not a save container")
        return False

    rows = read_rows(data, base)
    watched = rows[WATCHED_ACTION]
    print(f"{path.name}  written {stamp}  table at {base:#x}")
    for action, name in sorted(ACTIONS.items()):
        pad, keyboard, _, mouse, _ = rows[action]
        print(f"  row {action:#04x}  pad {pad:>6}  keyboard {keyboard:>4}  mouse {mouse:>4}  {name}")

    unbound = [action for action, row in enumerate(rows) if row[0] == UNBOUND]
    print(f"  rows with no pad button: {[hex(a) for a in unbound] or 'none'}")
    if watched[0] == UNBOUND:
        print(
            f"  row {WATCHED_ACTION:#04x} is UNBOUND on disk where the game's default is "
            f"{WATCHED_DEFAULT_PAD} -- the damage is stored, and every session will start broken"
        )
        return False
    print(
        f"  row {WATCHED_ACTION:#04x} holds {watched[0]} on disk, so a dead button in a running "
        "game was not loaded from this file"
    )
    return True


def selftest() -> int:
    """Build a container-shaped buffer in memory and prove the locator finds the table in it."""
    filler = bytes(range(256)) * 40
    defaults = {0x0D: 2000, 0x0E: 2001, 0x0F: 2003, 0x10: 2002}
    table = bytearray()
    for row in range(ROW_COUNT):
        table += struct.pack("<5i", defaults.get(row, UNBOUND), 100 + row, 0, 0, 0)
    assert len(table) == ROW_COUNT * ROW_STRIDE, len(table)

    healthy = filler + bytes(table) + filler
    base = find_table(healthy)
    assert base == len(filler), (base, len(filler))
    assert read_rows(healthy, base)[WATCHED_ACTION][0] == WATCHED_DEFAULT_PAD

    # The case the script exists for: the watched row is dead and the table must still be found,
    # which is why 0x0f is not in the signature.
    damaged = bytearray(healthy)
    struct.pack_into("<i", damaged, len(filler) + WATCHED_ACTION * ROW_STRIDE, UNBOUND)
    base = find_table(bytes(damaged))
    assert base == len(filler), base
    assert read_rows(bytes(damaged), base)[WATCHED_ACTION][0] == UNBOUND

    # A buffer with no table must report so rather than return a plausible offset.
    assert find_table(filler) is None

    # An anchor too close to the start cannot yield a negative base.
    assert find_table(struct.pack("<i", SIGNATURE[0x0D])) is None

    print("selftest: located the table healthy and damaged, refused two buffers without one")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", type=Path, action="append", help="a container to read")
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=DEFAULT_SAVE_DIR,
        help="directory searched when --save is not given (default: the Proton prefix's EldenRing)",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    saves = list(args.save or [])
    if not saves:
        if not args.save_dir.is_dir():
            print(f"no save directory at {args.save_dir}", file=sys.stderr)
            return 2
        for child in sorted(args.save_dir.rglob("*")):
            if child.suffix.lower() in SAVE_SUFFIXES and child.is_file():
                saves.append(child)
    if not saves:
        print(f"no {' or '.join(SAVE_SUFFIXES)} container under {args.save_dir}", file=sys.stderr)
        return 2

    healthy = True
    for path in saves:
        if not path.is_file():
            print(f"{path}: not a file", file=sys.stderr)
            healthy = False
            continue
        healthy &= report(path)
        print()
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
