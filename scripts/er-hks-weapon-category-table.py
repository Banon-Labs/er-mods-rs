#!/usr/bin/env python3
"""Read the `WeaponCategoryID` guard table out of Elden Ring's compiled `common_define.hks`.

The file is Havok Script bytecode: a Lua 5.1 descendant, big-endian, with its own
header, function layout and opcode numbering. The layout below follows
DSLuaDecompiler (`LuaDecompilerCore/LuaFile.cs` and `HksDecompiler.cs`, katalash):

- header: `\\x1bLua`, version `0x51`, format `0x0e`, then sizes, then a type-name
  table; the main function starts at file offset `0xee`.
- function: i32 unk, u32 params, u8 unk, u32 slots, u32 unk, i32 instruction
  count, pad to 4, instructions, i32 constant count, constants (tag byte: 0 nil,
  1 bool, 3 number as f32 when the header's number size is 4, 4 string with a u64
  length that includes the terminating NUL).
- instruction: opcode = bits 25..31, A = bits 0..7, C = bits 8..16,
  B = bits 17..25, Bx = bits 8..24. A B or C with bit 8 set names constant
  `value & 0xff` rather than a register.

The main chunk of `common_define.hks` is straight-line global definitions, so the
table is recovered by executing that chunk symbolically: registers hold constants,
global values, or tables under construction, and every `SETGLOBAL` records the
value it stores. `IsWeaponCanGuard` in `c0000.hks` reads row `[1]` as the
category, `[2]` for the left-hand weapon while one-handing (`HAND_RIGHT`) and `[3]`
for the two-handed weapon.

Usage:
    python3 scripts/er-hks-weapon-category-table.py [--hks <path>] [--json]
    python3 scripts/er-hks-weapon-category-table.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys

DEFAULT_HKS = os.path.expanduser(
    "~/er-extract/LOOK_HERE_ALL_ASSETS_20260713/action/script/common_define.hks"
)
MAIN_FUNCTION_OFFSET = 0xEE

OP_GETFIELD = 0
OP_GETGLOBAL = 6
OP_MOVE = 7
OP_RETURN = 9
OP_LOADBOOL = 13
OP_SETFIELD = 15
OP_SETTABLE_S = 16
OP_SETTABLE_S_BK = 17
OP_SETTABLE_N = 18
OP_SETTABLE_N_BK = 19
OP_SETTABLE = 20
OP_SETTABLE_BK = 21
OP_LOADK = 25
OP_LOADNIL = 26
OP_SETGLOBAL = 27
OP_JMP = 28
OP_UNM = 53
OP_NEWTABLE = 52
OP_SETLIST = 64
OP_CLOSURE = 66
OP_DATA = 76
OP_GETGLOBAL_MEM = 91

LFIELDS_PER_FLUSH = 50


class Unknown:
    """A register value the symbolic execution does not model."""

    def __init__(self, why: str) -> None:
        self.why = why

    def __repr__(self) -> str:
        return f"<unknown {self.why}>"


class Table:
    def __init__(self) -> None:
        self.array: dict[int, object] = {}
        self.hash: dict[object, object] = {}

    def as_list(self) -> list[object]:
        n = 0
        while (n + 1) in self.array:
            n += 1
        return [self.array[i] for i in range(1, n + 1)]


class Reader:
    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError(f"read past end at 0x{self.pos:x} (+{n})")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self.take(8))[0]


def parse_main_function(data: bytes) -> tuple[list[int], list[object]]:
    if data[:4] != b"\x1bLua" or data[4] != 0x51 or data[5] != 0x0E:
        raise ValueError("not a Havok Script 5.1 chunk (want \\x1bLua 0x51 0x0e)")
    if data[6] != 0:
        raise ValueError(f"endianness byte {data[6]} is not the big-endian 0 this reader handles")
    number_size = data[10]
    r = Reader(data, MAIN_FUNCTION_OFFSET)
    r.i32()
    r.u32()
    r.u8()
    r.u32()
    r.u32()
    count = r.i32()
    r.pos = (r.pos + 3) & ~3
    code = list(struct.unpack(f">{count}I", r.take(count * 4)))
    constants: list[object] = []
    for _ in range(r.i32()):
        tag = r.u8()
        if tag == 0:
            constants.append(None)
        elif tag == 1:
            constants.append(bool(r.u8()))
        elif tag == 3:
            fmt = ">f" if number_size == 4 else ">d"
            constants.append(struct.unpack(fmt, r.take(number_size))[0])
        elif tag == 4:
            length = r.u64()
            raw = r.take(length)
            if length and raw[-1] != 0:
                raise ValueError(f"string constant at 0x{r.pos:x} is not NUL-terminated")
            constants.append(raw[:-1].decode("utf-8"))
        else:
            raise ValueError(f"unknown constant tag {tag} at 0x{r.pos - 1:x}")
    return code, constants


def decode(ins: int) -> tuple[int, int, int, int, int]:
    op = (ins & 0xFF000000) >> 25
    a = ins & 0xFF
    c = (ins & 0x1FF00) >> 8
    b = (ins & 0x1FE0000) >> 17
    bx = (ins & 0x1FFFF00) >> 8
    return op, a, b, c, bx


def run_main_chunk(code: list[int], constants: list[object]) -> dict[str, object]:
    """Execute the main chunk's straight-line code symbolically and return its globals."""
    regs: dict[int, object] = {}
    globals_: dict[str, object] = {}

    def rk(v: int) -> object:
        if v & 0x100:
            return constants[v & 0xFF]
        return regs.get(v, Unknown(f"r{v} unset"))

    for pc, ins in enumerate(code):
        op, a, b, c, bx = decode(ins)
        if op == OP_LOADK:
            regs[a] = constants[bx]
        elif op in (OP_GETGLOBAL, OP_GETGLOBAL_MEM):
            name = constants[bx]
            regs[a] = globals_.get(name, Unknown(f"global {name} not set yet"))
        elif op == OP_SETGLOBAL:
            globals_[constants[bx]] = regs.get(a, Unknown(f"r{a} unset at pc {pc}"))
        elif op == OP_LOADBOOL:
            regs[a] = b == 1
        elif op == OP_LOADNIL:
            for reg in range(a, b + 1):
                regs[reg] = None
        elif op == OP_MOVE:
            regs[a] = regs.get(b, Unknown(f"r{b} unset"))
        elif op == OP_NEWTABLE:
            regs[a] = Table()
        elif op == OP_UNM:
            src = rk(b)
            regs[a] = -src if isinstance(src, float) else Unknown("unm of non-number")
        elif op == OP_SETLIST:
            table = regs.get(a)
            if not isinstance(table, Table):
                raise ValueError(f"SETLIST at pc {pc} on a non-table register r{a}")
            if b == 0:
                raise ValueError(f"SETLIST with B=0 (multret) at pc {pc} is not modelled")
            block = c & 0xFF
            for j in range(1, b + 1):
                table.array[(block - 1) * LFIELDS_PER_FLUSH + j] = regs.get(a + j)
        elif op in (OP_SETFIELD, OP_SETTABLE_S, OP_SETTABLE_S_BK, OP_SETTABLE,
                    OP_SETTABLE_BK, OP_SETTABLE_N, OP_SETTABLE_N_BK):
            table = regs.get(a)
            if isinstance(table, Table):
                key = constants[b] if op == OP_SETTABLE_S_BK else rk(b)
                value = rk(c)
                if isinstance(key, float) and key.is_integer():
                    table.array[int(key)] = value
                else:
                    table.hash[key] = value
        elif op == OP_DATA:
            # Operand word for the preceding instruction (a `GETGLOBAL_MEM` cache
            # slot, a closure upvalue binding); it writes no register.
            pass
        elif op == OP_RETURN:
            break
        else:
            regs[a] = Unknown(f"op {op} at pc {pc}")
    return globals_


def as_int(v: object) -> object:
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def extract(path: str) -> dict:
    data = open(path, "rb").read()
    code, constants = parse_main_function(data)
    g = run_main_chunk(code, constants)
    table = g.get("WeaponCategoryID")
    if not isinstance(table, Table):
        raise ValueError(f"WeaponCategoryID did not resolve to a table: {table!r}")
    by_value: dict[int, list[str]] = {}
    for name, value in g.items():
        if isinstance(name, str) and name.startswith("WEAPON_CATEGORY_"):
            by_value.setdefault(as_int(value), []).append(name)
    rows = []
    for index, row in enumerate(table.as_list(), 1):
        if not isinstance(row, Table):
            raise ValueError(f"WeaponCategoryID[{index}] is not a table: {row!r}")
        cells = [as_int(v) for v in row.as_list()]
        category = cells[0] if cells else None
        rows.append({
            "index": index,
            "category": category,
            "names": by_value.get(category, []),
            "cells": cells,
        })
    return {
        "TRUE": as_int(g.get("TRUE")),
        "FALSE": as_int(g.get("FALSE")),
        "rows": rows,
        "categories": {n: as_int(v) for n, v in g.items()
                       if isinstance(n, str) and n.startswith("WEAPON_CATEGORY_")},
        "instructions": len(code),
        "constants": len(constants),
    }


def truth(v: object, t: object, f: object) -> str:
    if v == t:
        return "TRUE"
    if v == f:
        return "FALSE"
    return repr(v)


def print_table(result: dict) -> None:
    t, f = result["TRUE"], result["FALSE"]
    print(f"# TRUE={t!r} FALSE={f!r}; {len(result['rows'])} rows; "
          f"main chunk {result['instructions']} instructions, {result['constants']} constants")
    print("idx  cat  name                                   [2] 1h-left  [3] 2h")
    for row in result["rows"]:
        cells = row["cells"]
        name = "/".join(row["names"]) or "?"
        c2 = truth(cells[1], t, f) if len(cells) > 1 else "-"
        c3 = truth(cells[2], t, f) if len(cells) > 2 else "-"
        extra = f"  extra={cells[3:]}" if len(cells) > 3 else ""
        print(f"{row['index']:>3}  {row['category']!s:>3}  {name:<38} {c2:<12} {c3}{extra}")
    listed = {row["category"] for row in result["rows"]}
    missing = sorted((v, n) for n, v in result["categories"].items() if v not in listed)
    if missing:
        print("# WEAPON_CATEGORY_* globals with no row (IsWeaponCanGuard returns nil for these):")
        for v, n in missing:
            print(f"#   {v:>3}  {n}")


def selftest(path: str) -> int:
    failures = []
    # Instruction field layout, checked on a hand-assembled word: LOADK A=3 Bx=0x1234.
    op, a, _, _, bx = decode((OP_LOADK << 25) | (0x1234 << 8) | 3)
    if (op, a, bx) != (OP_LOADK, 3, 0x1234):
        failures.append(f"decode round-trip gave {(op, a, bx)}")
    if not os.path.exists(path):
        print(f"SKIP: {path} absent; only the decoder check ran")
        return 1 if failures else 0
    result = extract(path)
    t, f = result["TRUE"], result["FALSE"]
    if t is None or f is None or t == f:
        failures.append(f"TRUE/FALSE not distinct constants: {t!r}/{f!r}")
    rows = {row["category"]: row for row in result["rows"]}
    cats = result["categories"]

    def row_for(name: str) -> dict | None:
        value = cats.get(name)
        if value is None:
            failures.append(f"{name} is not defined in the chunk")
            return None
        row = rows.get(value)
        if row is None:
            failures.append(f"{name}={value} has no WeaponCategoryID row")
        return row

    # Every row is three cells: category, one-hand-left, two-hand.
    for row in result["rows"]:
        if len(row["cells"]) != 3:
            failures.append(f"row {row['index']} has {len(row['cells'])} cells")
    # Shields exist to block; a shield row that says `FALSE` would mean the columns are misread.
    for name in ("WEAPON_CATEGORY_LARGE_SHIELD", "WEAPON_CATEGORY_SMALL_SHIELD",
                 "WEAPON_CATEGORY_MIDDLE_SHIELD"):
        row = row_for(name)
        if row and row["cells"][1:] != [t, t]:
            failures.append(f"{name} row {row['cells']} is not guard-capable in both columns")
    # The shield numbers match the independent AI enum in Smithbox's Enums.txt
    # (PLAN_WEAPON_CATEGORY_LARGE/SMALL/MIDDLE_SHIELD = 47/48/49).
    for name, want in (("WEAPON_CATEGORY_LARGE_SHIELD", 47), ("WEAPON_CATEGORY_SMALL_SHIELD", 48),
                       ("WEAPON_CATEGORY_MIDDLE_SHIELD", 49), ("WEAPON_CATEGORY_STAFF", 41)):
        if cats.get(name) != want:
            failures.append(f"{name}={cats.get(name)!r}, Enums.txt PLAN_ value is {want}")
    for failure in failures:
        print(f"FAIL: {failure}")
    if not failures:
        print(f"selftest ok: {len(result['rows'])} rows, TRUE={t!r} FALSE={f!r}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hks", default=DEFAULT_HKS, help="compiled common_define.hks")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--selftest", action="store_true", help="run the consistency checks")
    args = parser.parse_args()
    if args.selftest:
        return selftest(args.hks)
    result = extract(args.hks)
    if args.json:
        json.dump(result, sys.stdout, indent=2, default=repr)
        print()
    else:
        print_table(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
