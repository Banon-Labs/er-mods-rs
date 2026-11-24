#!/usr/bin/env python3
"""List where a PE reads keys, and which virtual key each read names.

Static answer to "who reads this key", for a DLL with no source here (ersc.dll, a third party's
mod). Finds the user32 key-state imports, every rip-relative use of their IAT slots, and any
`mov ecx, imm32` in the 24 bytes before such a call, which is the virtual key on win64.

    python3 scripts/pe-key-reads.py <dll> [--vk 0x24]

Standard library only. A site whose key is computed shows `vk=?`; those need a decompile.
"""

import argparse
import re
import struct
from pathlib import Path

KEY_IMPORTS = {"GetAsyncKeyState", "GetKeyState", "GetKeyboardState", "RegisterHotKey"}
MEMORY_LOADS = rb"\xff\x15|\xff\x25|\x48\x8b[\x05\x0d\x15\x1d\x2d\x35\x3d]|\x4c\x8b[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]"


def sections(image):
    pe = struct.unpack_from("<I", image, 0x3C)[0]
    count = struct.unpack_from("<H", image, pe + 6)[0]
    optional = struct.unpack_from("<H", image, pe + 20)[0]
    out = []
    for index in range(count):
        at = pe + 24 + optional + 40 * index
        virtual_size, rva, raw_size, raw_at = struct.unpack_from("<IIII", image, at + 8)
        flags = struct.unpack_from("<I", image, at + 36)[0]
        out.append((rva, virtual_size, raw_at, raw_size, flags))
    return pe, out


def to_offset(secs, rva):
    for start, virtual_size, raw_at, raw_size, _ in secs:
        if start <= rva < start + max(virtual_size, raw_size):
            return rva - start + raw_at
    return None


def c_string(image, at):
    return image[at : at + 128].split(b"\0")[0].decode("latin1")


def key_import_slots(image, pe, secs):
    table_rva = struct.unpack_from("<I", image, pe + 24 + 120)[0]
    at = to_offset(secs, table_rva)
    slots = {}
    while at is not None:
        names, _, _, dll_name, thunks = struct.unpack_from("<IIIII", image, at)
        if not dll_name:
            break
        walk = to_offset(secs, names or thunks)
        index = 0
        while True:
            value = struct.unpack_from("<Q", image, walk + index * 8)[0]
            if not value:
                break
            if not value >> 63:
                name = c_string(image, to_offset(secs, value & 0x7FFFFFFF) + 2)
                if name in KEY_IMPORTS:
                    slots[thunks + index * 8] = name
            index += 1
        at += 20
    return slots


def key_before(code, at):
    window = code[max(at - 24, 0) : at]
    found = None
    for match in re.finditer(rb"\xb9(....)", window, re.DOTALL):
        found = struct.unpack("<I", match.group(1))[0]
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("dll", type=Path)
    parser.add_argument("--vk", type=lambda text: int(text, 0), help="only sites naming this key")
    args = parser.parse_args()

    image = args.dll.read_bytes()
    pe, secs = sections(image)
    slots = key_import_slots(image, pe, secs)
    print(f"key imports: { {hex(slot): name for slot, name in slots.items()} }")

    for start, _, raw_at, raw_size, flags in secs:
        if not flags & 0x20000000:
            continue
        code = image[raw_at : raw_at + raw_size]
        for match in re.finditer(MEMORY_LOADS, code):
            operand = match.end()
            if operand + 4 > len(code):
                continue
            target = start + operand + 4 + struct.unpack_from("<i", code, operand)[0]
            if target not in slots:
                continue
            vk = key_before(code, match.start())
            if args.vk is None or vk == args.vk:
                named = "?" if vk is None else hex(vk)
                print(f"rva=0x{start + match.start():x} {slots[target]} vk={named} op={match.group(0).hex()}")


if __name__ == "__main__":
    main()
