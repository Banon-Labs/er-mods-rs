#!/usr/bin/env python3
"""Find direct `call`/`jmp` sites targeting an address inside the decrypted ersc runtime image.

`scripts/ersc-xrefs.py` walks the on-disk PE, which is Themida-packed: the networking code lives
in the compressed `ERSC` section and is not there to be walked. The flat runtime dump written by
`scripts/er-dump-ersc-image.py` holds the decrypted bytes, addressed as `VA = 0x180000000 + offset`.

    python3 scripts/ersc-runtime-xref.py 0x1800ad6e0
    python3 scripts/ersc-runtime-xref.py --selftest

This is a byte scan for the `e8`/`e9` rel32 encodings, so it finds direct calls and tail jumps and
nothing else. A target reached through a register or a vtable slot does not appear here, and the
absence of a hit is not proof the address is unreferenced.
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = ROOT / "vendor-archive" / "seamless" / "ersc-2.0.1.runtime.bin"
BASE = 0x180000000


def scan(data: bytes, target: int) -> list[tuple[int, str]]:
    hits = []
    for opcode, name in ((0xE8, "call"), (0xE9, "jmp")):
        start = 0
        while True:
            index = data.find(bytes([opcode]), start)
            if index < 0 or index + 5 > len(data):
                break
            start = index + 1
            rel = struct.unpack_from("<i", data, index + 1)[0]
            if BASE + index + 5 + rel == target:
                hits.append((BASE + index, name))
    return sorted(hits)


def selftest() -> int:
    # A call at VA 0x180000000 whose rel32 lands on 0x180000100.
    blob = bytearray(0x200)
    blob[0] = 0xE8
    blob[1:5] = struct.pack("<i", 0x100 - 5)
    assert scan(bytes(blob), 0x180000100) == [(BASE, "call")], "the rel32 arithmetic is wrong"
    assert scan(bytes(blob), 0x180000104) == [], "a near miss must not match"
    print("selftest ok -- rel32 call/jmp resolution agrees")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", help="the VA being called, e.g. 0x1800ad6e0")
    parser.add_argument("--image", type=pathlib.Path, default=DEFAULT_IMAGE)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.target is None:
        parser.error("a target VA is required")
    data = args.image.read_bytes()
    hits = scan(data, int(args.target, 0))
    for address, kind in hits:
        print(f"0x{address:x}  {kind}")
    print(f"{len(hits)} direct site(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
