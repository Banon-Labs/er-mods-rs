#!/usr/bin/env python3
"""Assert that er_cel_shade.dll does not import dxcompiler.dll or dxil.dll.

The shader editor compiles HLSL through dxcompiler.dll, which is fetched onto the user's machine
and never bundled (design `docs/plans/er-shader-editor.md`, sections 7.5 and 15). er-cel-shade
loads it by path with `LoadLibraryExW` and `GetProcAddress`, and only when the compiler helper
cannot start. A module that imports it instead fails to load wherever it is missing -- and for
er_cel_shade.dll that would be no cel shading at all for want of an optional editor. The `windows`
crate makes that mistake easy: its `DxcCreateInstance` is a link-time import of dxcompiler.dll,
so calling it once anywhere in the crate adds the import without a word from the compiler.

So this reads the built DLL's import table and its delay-load table, in pure Python with no
dependency, and fails when either names a forbidden module. Names compare without case, as the
Windows loader compares them.

Usage:
    python3 scripts/check-dll-imports.py                  # the default DLL
    python3 scripts/check-dll-imports.py --dll <path>     # any PE file, repeatable
    python3 scripts/check-dll-imports.py --list           # print every imported module as well
    python3 scripts/check-dll-imports.py --selftest

The default DLL is `target/x86_64-pc-windows-msvc/release/er_cel_shade.dll` in this checkout. The
plain form runs the synthetic PE cases of `--selftest` first, so the reader is proven even where
no DLL has been built, and then says plainly that it skipped the DLL when there is none. A file
that cannot be read as a PE fails: a reader that cannot see the import table has not shown that
the import is absent.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DLL = REPO / "target" / "x86_64-pc-windows-msvc" / "release" / "er_cel_shade.dll"

# The modules the DLL must never import. dxil.dll is the validator older dxcompilers needed beside
# them; the editor never loads it at all.
FORBIDDEN = ("dxcompiler.dll", "dxil.dll")

# Optional header magics.
PE32 = 0x10B
PE32_PLUS = 0x20B

# Data directory indexes.
IMPORT_DIRECTORY = 1
DELAY_IMPORT_DIRECTORY = 13

IMPORT_DESCRIPTOR_BYTES = 20
DELAY_DESCRIPTOR_BYTES = 32
SECTION_HEADER_BYTES = 40

# A descriptor table with no terminator inside this many entries is corrupt rather than large: a
# Windows DLL imports tens of modules.
MAX_DESCRIPTORS = 4096
# The longest module name read before it counts as unterminated.
MAX_NAME_BYTES = 1024

# `ImgDelayDescr::grAttrs` bit: the descriptor's addresses are RVAs. Without it they are virtual
# addresses, as old linkers wrote them.
DELAY_RVA_BASED = 1


class PeError(Exception):
    """The bytes cannot be read as a PE file far enough to list its imports."""


@dataclass(frozen=True)
class Section:
    virtual_address: int
    virtual_size: int
    raw_pointer: int
    raw_size: int


@dataclass(frozen=True)
class Imports:
    direct: list[str]
    delayed: list[str]

    def all(self) -> list[str]:
        return self.direct + self.delayed


def u16(data: bytes, at: int, what: str) -> int:
    if at < 0 or at + 2 > len(data):
        raise PeError(f"{what} at {at:#x} is past the end of the file")
    return struct.unpack_from("<H", data, at)[0]


def u32(data: bytes, at: int, what: str) -> int:
    if at < 0 or at + 4 > len(data):
        raise PeError(f"{what} at {at:#x} is past the end of the file")
    return struct.unpack_from("<I", data, at)[0]


def u64(data: bytes, at: int, what: str) -> int:
    if at < 0 or at + 8 > len(data):
        raise PeError(f"{what} at {at:#x} is past the end of the file")
    return struct.unpack_from("<Q", data, at)[0]


def file_offset(sections: list[Section], rva: int, what: str) -> int:
    """The file offset of `rva`, which must lie in a section's bytes on disk."""
    for section in sections:
        span = max(section.virtual_size, section.raw_size)
        if section.virtual_address <= rva < section.virtual_address + span:
            delta = rva - section.virtual_address
            if delta >= section.raw_size:
                raise PeError(f"{what} at RVA {rva:#x} is in a section's uninitialized tail")
            return section.raw_pointer + delta
    raise PeError(f"{what} at RVA {rva:#x} is in no section")


def c_string(data: bytes, at: int, what: str) -> str:
    end = data.find(b"\0", at, at + MAX_NAME_BYTES)
    if at < 0 or at >= len(data) or end < 0:
        raise PeError(f"{what} at {at:#x} is not a terminated string")
    try:
        return data[at:end].decode("ascii")
    except UnicodeDecodeError as error:
        raise PeError(f"{what} at {at:#x} is not ASCII: {error}") from None


def read_imports(data: bytes) -> Imports:
    """Every module the import and delay-load tables of a PE name, in table order."""
    if data[:2] != b"MZ":
        raise PeError("no MZ header")
    pe = u32(data, 0x3C, "e_lfanew")
    if data[pe:pe + 4] != b"PE\0\0":
        raise PeError(f"no PE signature at {pe:#x}")
    coff = pe + 4
    section_count = u16(data, coff + 2, "NumberOfSections")
    optional_size = u16(data, coff + 16, "SizeOfOptionalHeader")
    optional = coff + 20
    magic = u16(data, optional, "the optional header magic")
    if magic == PE32_PLUS:
        image_base = u64(data, optional + 24, "ImageBase")
        directory_count_at = optional + 108
    elif magic == PE32:
        image_base = u32(data, optional + 28, "ImageBase")
        directory_count_at = optional + 92
    else:
        raise PeError(f"optional header magic {magic:#x} is neither PE32 nor PE32+")
    directory_count = u32(data, directory_count_at, "NumberOfRvaAndSizes")
    directories = directory_count_at + 4

    def directory(index: int) -> tuple[int, int]:
        if index >= directory_count:
            return 0, 0
        at = directories + 8 * index
        if at + 8 > optional + optional_size:
            raise PeError(f"data directory {index} is outside the optional header")
        return u32(data, at, "a data directory RVA"), u32(data, at + 4, "a data directory size")

    sections = []
    for index in range(section_count):
        at = optional + optional_size + index * SECTION_HEADER_BYTES
        sections.append(Section(
            virtual_size=u32(data, at + 8, "VirtualSize"),
            virtual_address=u32(data, at + 12, "VirtualAddress"),
            raw_size=u32(data, at + 16, "SizeOfRawData"),
            raw_pointer=u32(data, at + 20, "PointerToRawData"),
        ))

    direct = []
    rva, _ = directory(IMPORT_DIRECTORY)
    if rva:
        at = file_offset(sections, rva, "the import directory")
        for index in range(MAX_DESCRIPTORS + 1):
            if index == MAX_DESCRIPTORS:
                raise PeError(f"the import directory has no terminator in {MAX_DESCRIPTORS} entries")
            entry = data[at:at + IMPORT_DESCRIPTOR_BYTES]
            if len(entry) < IMPORT_DESCRIPTOR_BYTES:
                raise PeError("the import directory runs past the end of the file")
            if entry == bytes(IMPORT_DESCRIPTOR_BYTES):
                break
            name_rva = u32(data, at + 12, "an import descriptor's Name")
            direct.append(c_string(data, file_offset(sections, name_rva, "an import name"),
                                   "an import name"))
            at += IMPORT_DESCRIPTOR_BYTES

    delayed = []
    rva, _ = directory(DELAY_IMPORT_DIRECTORY)
    if rva:
        at = file_offset(sections, rva, "the delay-load directory")
        for index in range(MAX_DESCRIPTORS + 1):
            if index == MAX_DESCRIPTORS:
                raise PeError(
                    f"the delay-load directory has no terminator in {MAX_DESCRIPTORS} entries")
            entry = data[at:at + DELAY_DESCRIPTOR_BYTES]
            if len(entry) < DELAY_DESCRIPTOR_BYTES:
                raise PeError("the delay-load directory runs past the end of the file")
            if entry == bytes(DELAY_DESCRIPTOR_BYTES):
                break
            attributes = u32(data, at, "a delay descriptor's attributes")
            name = u32(data, at + 4, "a delay descriptor's DllName")
            if not attributes & DELAY_RVA_BASED:
                name -= image_base
            delayed.append(c_string(data, file_offset(sections, name, "a delay-load name"),
                                    "a delay-load name"))
            at += DELAY_DESCRIPTOR_BYTES
    return Imports(direct=direct, delayed=delayed)


def forbidden_in(imports: Imports, forbidden: tuple[str, ...] = FORBIDDEN) -> list[str]:
    """The imported module names that are forbidden, as the file spells them."""
    banned = {name.lower() for name in forbidden}
    return [name for name in imports.all() if name.lower() in banned]


def check_file(path: Path, show: bool) -> bool:
    """True when `path` is a readable PE that imports no forbidden module."""
    try:
        imports = read_imports(path.read_bytes())
    except (OSError, PeError) as error:
        print(f"check-dll-imports: FAIL  {path}: cannot read its import table: {error}")
        return False
    if show:
        for name in imports.direct:
            print(f"  import  {name}")
        for name in imports.delayed:
            print(f"  delay   {name}")
    found = forbidden_in(imports)
    if found:
        print(f"check-dll-imports: FAIL  {path} imports {', '.join(found)}; it must load the "
              "compiler by path (LoadLibraryExW + GetProcAddress) so it still loads without it")
        return False
    print(f"check-dll-imports: ok  {path.name} imports {len(imports.direct)} module(s) and "
          f"delay-loads {len(imports.delayed)}, none of {', '.join(FORBIDDEN)}")
    return True


# ---------------------------------------------------------------------------------------------
# The selftest: synthetic PE files built here, so it needs no DLL and no toolchain.

SECTION_RVA = 0x1000
SECTION_RAW = 0x400
SECTION_SIZE = 0x1000
# Where the synthetic section keeps the names, the import table and the delay-load table. The
# section is the end of the file, so a table that runs on runs off the end of the file.
NAMES_AT = 0x000
IMPORT_AT = 0x400
DELAY_AT = 0x600


def build_pe(direct: list[str], delayed: list[str] = (), *, pe32: bool = False,
             delay_va: bool = False, terminate: bool = True,
             image_base: int = 0x1_8000_0000) -> bytes:
    """A minimal PE whose one section holds the import and delay-load tables and the names."""
    section = bytearray(SECTION_SIZE)
    names = NAMES_AT
    name_rvas = {}
    for name in [*direct, *delayed]:
        if name in name_rvas:
            continue
        encoded = name.encode("ascii") + b"\0"
        section[names:names + len(encoded)] = encoded
        name_rvas[name] = SECTION_RVA + names
        names += len(encoded)

    import_at = IMPORT_AT
    for index, name in enumerate(direct):
        struct.pack_into("<5I", section, import_at + index * IMPORT_DESCRIPTOR_BYTES,
                         0, 0, 0, name_rvas[name], 0)
    if not terminate:
        # Copies of the first entry to the end of the section, and no all-zero one.
        count = (SECTION_SIZE - import_at) // IMPORT_DESCRIPTOR_BYTES
        for index in range(len(direct), count):
            struct.pack_into("<5I", section, import_at + index * IMPORT_DESCRIPTOR_BYTES,
                             0, 0, 0, name_rvas[direct[0]], 0)
    delay_at = DELAY_AT
    for index, name in enumerate(delayed):
        attributes = 0 if delay_va else DELAY_RVA_BASED
        rva = name_rvas[name]
        dll_name = rva + image_base if delay_va else rva
        struct.pack_into("<8I", section, delay_at + index * DELAY_DESCRIPTOR_BYTES,
                         attributes, dll_name & 0xFFFF_FFFF, 0, 0, 0, 0, 0, 0)

    optional_size = 224 if pe32 else 240
    pe = 0x80
    headers = bytearray(SECTION_RAW)
    headers[0:2] = b"MZ"
    struct.pack_into("<I", headers, 0x3C, pe)
    headers[pe:pe + 4] = b"PE\0\0"
    coff = pe + 4
    struct.pack_into("<HHIIIHH", headers, coff, 0x14C if pe32 else 0x8664, 1, 0, 0, 0,
                     optional_size, 0x2022)
    optional = coff + 20
    if pe32:
        struct.pack_into("<H", headers, optional, PE32)
        struct.pack_into("<I", headers, optional + 28, image_base & 0xFFFF_FFFF)
        count_at = optional + 92
    else:
        struct.pack_into("<H", headers, optional, PE32_PLUS)
        struct.pack_into("<Q", headers, optional + 24, image_base)
        count_at = optional + 108
    struct.pack_into("<I", headers, count_at, 16)
    directories = count_at + 4
    if direct:
        struct.pack_into("<II", headers, directories + 8 * IMPORT_DIRECTORY,
                         SECTION_RVA + import_at,
                         (len(direct) + 1) * IMPORT_DESCRIPTOR_BYTES)
    if delayed:
        struct.pack_into("<II", headers, directories + 8 * DELAY_IMPORT_DIRECTORY,
                         SECTION_RVA + delay_at, (len(delayed) + 1) * DELAY_DESCRIPTOR_BYTES)
    header = optional + optional_size
    headers[header:header + 8] = b".idata\0\0"
    struct.pack_into("<IIII", headers, header + 8, SECTION_SIZE, SECTION_RVA, SECTION_SIZE,
                     SECTION_RAW)
    return bytes(headers) + bytes(section)


def selftest(quiet: bool = False) -> int:
    failures = []

    def expect(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")
        elif not quiet:
            print(f"ok  {name}")

    def refused(name: str, data: bytes, says: str) -> None:
        try:
            read_imports(data)
        except PeError as error:
            if says in str(error):
                expect(name, True, True)
            else:
                failures.append(f"{name}: refused, but said {error}")
            return
        failures.append(f"{name}: read without an error")

    clean = ["KERNEL32.dll", "ntdll.dll", "combase.dll", "OLEAUT32.dll"]
    imports = read_imports(build_pe(clean))
    expect("every direct import, in table order", imports.direct, clean)
    expect("no delay-load table, no delayed imports", imports.delayed, [])
    expect("a DLL importing only system modules passes", forbidden_in(imports), [])

    linked = read_imports(build_pe(["KERNEL32.dll", "DXCOMPILER.DLL"]))
    expect("an import of dxcompiler is found whatever its case", forbidden_in(linked),
           ["DXCOMPILER.DLL"])
    delayed = read_imports(build_pe(["KERNEL32.dll"], ["dxil.dll"]))
    expect("a delay-loaded forbidden module counts", forbidden_in(delayed), ["dxil.dll"])
    # Old linkers wrote virtual addresses, which fit the 32-bit field only in a 32-bit image.
    old = read_imports(build_pe(["KERNEL32.dll"], ["dxcompiler.dll"], pe32=True, delay_va=True,
                                image_base=0x1000_0000))
    expect("a delay descriptor with virtual addresses is read through the image base",
           old.delayed, ["dxcompiler.dll"])
    narrow = read_imports(build_pe(["KERNEL32.dll", "dxcompiler.dll"], pe32=True))
    expect("a PE32 file is read too", narrow.direct, ["KERNEL32.dll", "dxcompiler.dll"])
    expect("a DLL with no imports at all has none", read_imports(build_pe([])).all(), [])
    expect("a similar name is not a forbidden one",
           forbidden_in(read_imports(build_pe(["dxcompiler.dll.bak", "mydxil.dll"]))), [])

    refused("bytes that are no PE are refused", b"hello, world", "no MZ header")
    good = build_pe(clean)
    refused("a file cut inside its headers is refused", good[:0x90], "past the end")
    moved = bytearray(good)
    struct.pack_into("<I", moved, 0x3C, len(good) + 0x100)
    refused("a PE header past the end is refused", bytes(moved), "no PE signature")
    stray = bytearray(good)
    optional = 0x80 + 4 + 20
    struct.pack_into("<I", stray, optional + 112 + 8 * IMPORT_DIRECTORY, 0x9000)
    refused("an import table in no section is refused", bytes(stray), "in no section")
    refused("an import table with no terminator is refused",
            build_pe(["KERNEL32.dll"], terminate=False), "runs past the end")
    unterminated = bytearray(build_pe(["KERNEL32.dll"]))
    name = SECTION_RAW + NAMES_AT
    unterminated[name:name + MAX_NAME_BYTES] = b"x" * MAX_NAME_BYTES
    refused("a module name with no terminator is refused", bytes(unterminated),
            "not a terminated string")

    if not quiet:
        for line in failures:
            print(f"FAIL  {line}")
        print("check-dll-imports selftest: " + ("FAILED" if failures else "passed"))
    elif failures:
        for line in failures:
            print(f"check-dll-imports: selftest FAIL  {line}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dll", type=Path, action="append",
                        help=f"a PE file to check (default {DEFAULT_DLL.relative_to(REPO)})")
    parser.add_argument("--list", action="store_true", help="print every imported module")
    parser.add_argument("--selftest", action="store_true")
    options = parser.parse_args()
    if options.selftest:
        return selftest()
    if selftest(quiet=True):
        return 1
    paths = options.dll or [DEFAULT_DLL]
    ok = True
    for path in paths:
        if not path.is_file():
            if options.dll:
                print(f"check-dll-imports: FAIL  {path} does not exist")
                ok = False
            else:
                print(f"check-dll-imports: skip  no built DLL at {path}; the synthetic PE cases "
                      "passed, the import table of the real DLL was not read")
            continue
        ok = check_file(path, options.list) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
