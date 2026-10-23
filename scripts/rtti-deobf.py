#!/usr/bin/env python3
"""Given a vtable slot VA (where some method ptr sits) in the deobfuscated ER mapped image,
scan backward to the vtable base (where [base-8] is a valid MSVC RTTI CompleteObjectLocator),
and print the class name + the slot index of the queried address.

Mapped image: file offset == RVA, image base 0x140000000 (VA = offset + base).
MSVC x64 RTTI: vtable[-1] -> COL (abs VA); COL+0x0C = RVA(TypeDescriptor);
TypeDescriptor+0x10 = null-terminated mangled name (".?AVClass@NS@@").
Usage: rtti-deobf.py 0x142a81cb0 [more slots...]
The image is ER_DEOBF_BIN, default eldenring-deobf-1.17.2.bin (the installed 2.7.2.0 build).
"""
import sys, struct, os

BASE = 0x140000000


def find_image(name="eldenring-deobf-1.17.2.bin"):
    """`ER_DEOBF_BIN` when set, else `name` at the repo root or the nearest directory above it.

    The images are gitignored, so a linked worktree under `.worktrees/` reads the main
    checkout's.
    """
    if os.environ.get("ER_DEOBF_BIN"):
        return os.environ["ER_DEOBF_BIN"]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = root
    while True:
        if os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
        if os.path.dirname(d) == d:
            return os.path.join(root, name)
        d = os.path.dirname(d)


IMG = find_image()
MAX_BACK = 0x4000  # scan up to 2048 slots back


def main():
    data = open(IMG, "rb").read()
    n = len(data)

    def rd_q(va):
        off = va - BASE
        return struct.unpack_from("<Q", data, off)[0] if 0 <= off and off + 8 <= n else None

    def rd_d(va):
        off = va - BASE
        return struct.unpack_from("<I", data, off)[0] if 0 <= off and off + 4 <= n else None

    def rd_cstr(va):
        off = va - BASE
        if not (0 <= off < n):
            return None
        return data[off : data.find(b"\x00", off)].decode("latin1", "replace")

    def class_at(base):
        """If [base-8] is a valid COL, return its class name else None."""
        col = rd_q(base - 8)
        if not col or not (BASE <= col < BASE + n):
            return None
        sig = rd_d(col)  # 0 (x86) or 1 (x64)
        if sig not in (0, 1):
            return None
        td_rva = rd_d(col + 0x0C)
        if not td_rva:
            return None
        name = rd_cstr(BASE + td_rva + 0x10)
        return name if name and name.startswith(".?A") else None

    for arg in sys.argv[1:]:
        slot = int(arg, 16)
        found = None
        b = slot
        while b >= slot - MAX_BACK:
            nm = class_at(b)
            if nm:
                found = (b, nm, (slot - b) // 8)
                break
            b -= 8
        if found:
            base, nm, idx = found
            print(f"slot 0x{slot:x}: vtable base 0x{base:x} slot[+0x{idx*8:x}] class {nm}")
        else:
            print(f"slot 0x{slot:x}: no vtable base found within 0x{MAX_BACK:x}")


if __name__ == "__main__":
    main()
