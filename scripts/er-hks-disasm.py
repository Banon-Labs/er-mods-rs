#!/usr/bin/env python3
"""Disassemble an Elden Ring HavokScript (HKS) compiled chunk and trace SetTurnSpeed.

The player's behaviour script, `action/script/c0000.hks`, is a HavokScript
chunk: a Lua 5.1 derivative with a big-endian header, a type-name table, its
own opcode numbering and 7-bit opcodes.  This tool parses the whole chunk
(every function prototype, its constants, instructions and debug info) and
then reports every call that sets the player's turn speed.

Layout, each piece checked against the file itself (see `--selftest`):

  header   "\\x1bLua" 0x51 0x0e endian=0(big) int=4 size_t=8 instr=4 number=4
           integral=0 flags(1) pad(1) typecount(u32) {id u32, len u32, name}
  function upvals u32, params u32, vararg u8, framesize u32, unk u32,
           ninstr u32, pad to 4, ninstr * u32 instructions,
           nconst u32, {type u8, value} (0 nil, 1 bool u8, 3 float f32,
           4 string u64 len + bytes incl. NUL, 2/11 u64),
           hasdebug u32 [debug block], nchild u32, children...
  debug    nlines u32, nlocals u32, nupnames u32, linebegin u32, lineend u32,
           path str, name str, nlines * u32, nlocals * {str, u32, u32},
           nupnames * str

Instruction fields (HKS): op = i >> 25, A = i & 0xff, C = (i >> 8) & 0x1ff,
B = (i >> 17) & 0xff, Bx = (i >> 8) & 0x1ffff, sBx = Bx - 0xffff.

Usage (`file` defaults to c0000.hks, overridable with `ER_HKS_FILE`):
  `python3 scripts/er-hks-disasm.py [file] --turn-speed`
  `python3 scripts/er-hks-disasm.py [file] --list`
  `python3 scripts/er-hks-disasm.py [file] --dump <name> --calls-to <name> --reaches <name>`
  `python3 scripts/er-hks-disasm.py --selftest`
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from dataclasses import dataclass, field

DEFAULT_FILE = os.environ.get(
    "ER_HKS_FILE",
    os.path.join(
        os.path.expanduser("~"),
        "er-extract/LOOK_HERE_ALL_ASSETS_20260713/action/script/c0000.hks",
    ),
)

OPNAMES = [
    "GETFIELD", "TEST", "CALL_I", "CALL_C", "EQ", "EQ_BK", "GETGLOBAL", "MOVE",
    "SELF", "RETURN", "GETTABLE_S", "GETTABLE_N", "GETTABLE", "LOADBOOL",
    "TFORLOOP", "SETFIELD", "SETTABLE_S", "SETTABLE_S_BK", "SETTABLE_N",
    "SETTABLE_N_BK", "SETTABLE", "SETTABLE_BK", "TAILCALL_I", "TAILCALL_C",
    "TAILCALL_M", "LOADK", "LOADNIL", "SETGLOBAL", "JMP", "CALL_M", "CALL",
    "INTRINSIC_INDEX", "INTRINSIC_NEWINDEX", "INTRINSIC_SELF",
    "INTRINSIC_INDEX_LITERAL", "INTRINSIC_NEWINDEX_LITERAL",
    "INTRINSIC_SELF_LITERAL", "TAILCALL", "GETUPVAL", "SETUPVAL", "ADD",
    "ADD_BK", "SUB", "SUB_BK", "MUL", "MUL_BK", "DIV", "DIV_BK", "MOD",
    "MOD_BK", "POW", "POW_BK", "NEWTABLE", "UNM", "NOT", "LEN", "LT", "LT_BK",
    "LE", "LE_BK", "CONCAT", "TESTSET", "FORPREP", "FORLOOP", "SETLIST",
    "CLOSE", "CLOSURE", "VARARG", "TAILCALL_I_R1", "CALL_I_R1", "SETUPVAL_R1",
    "TEST_R1", "NOT_R1", "GETFIELD_R1", "SETFIELD_R1", "NEWSTRUCT", "DATA",
    "SETSLOTN", "SETSLOTI", "SETSLOT", "SETSLOTS", "SETSLOTMT", "CHECKTYPE",
    "CHECKTYPES", "GETSLOT", "GETSLOTMT", "SELFSLOT", "SELFSLOTMT",
    "GETFIELD_MM", "CHECKTYPE_D", "GETSLOT_D", "GETGLOBAL_MEM",
]
OP = {n: i for i, n in enumerate(OPNAMES)}
CALL_OPS = {OP[n] for n in ("CALL_I", "CALL_C", "CALL_M", "CALL", "CALL_I_R1",
                             "TAILCALL_I", "TAILCALL_C", "TAILCALL_M",
                             "TAILCALL", "TAILCALL_I_R1")}


class ParseError(Exception):
    pass


@dataclass
class Instr:
    raw: int

    @property
    def op(self) -> int:
        return self.raw >> 25

    @property
    def a(self) -> int:
        return self.raw & 0xFF

    @property
    def b(self) -> int:
        return (self.raw >> 17) & 0xFF

    @property
    def c(self) -> int:
        return (self.raw >> 8) & 0x1FF

    @property
    def bx(self) -> int:
        return (self.raw >> 8) & 0x1FFFF

    @property
    def sbx(self) -> int:
        return self.bx - 0xFFFF

    @property
    def name(self) -> str:
        return OPNAMES[self.op] if self.op < len(OPNAMES) else f"OP{self.op}"


@dataclass
class Func:
    offset: int
    upvals: int
    params: int
    vararg: int
    framesize: int
    instrs: list[Instr]
    consts: list
    debug_name: str | None = None
    debug_path: str | None = None
    lines: list[int] = field(default_factory=list)
    locals: list[str] = field(default_factory=list)
    children: list["Func"] = field(default_factory=list)
    parent: "Func | None" = None
    name: str = "?"
    index: int = -1


class Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def u8(self) -> int:
        if self.p + 1 > len(self.d):
            raise ParseError(f"eof reading u8 at {self.p:#x}")
        v = self.d[self.p]
        self.p += 1
        return v

    def u32(self) -> int:
        if self.p + 4 > len(self.d):
            raise ParseError(f"eof reading u32 at {self.p:#x}")
        v = struct.unpack_from(">I", self.d, self.p)[0]
        self.p += 4
        return v

    def u64(self) -> int:
        if self.p + 8 > len(self.d):
            raise ParseError(f"eof reading u64 at {self.p:#x}")
        v = struct.unpack_from(">Q", self.d, self.p)[0]
        self.p += 8
        return v

    def f32(self) -> float:
        v = struct.unpack_from(">f", self.d, self.p)[0]
        self.p += 4
        return v

    def string(self) -> str | None:
        n = self.u64()
        if n == 0:
            return None
        if n > 1 << 20 or self.p + n > len(self.d):
            raise ParseError(f"implausible string length {n} at {self.p - 8:#x}")
        raw = self.d[self.p:self.p + n]
        self.p += n
        if raw[-1] != 0:
            raise ParseError(f"string at {self.p - n:#x} not NUL-terminated")
        return raw[:-1].decode("utf-8", errors="strict")


def parse_header(r: Reader) -> dict:
    if r.d[:5] != b"\x1bLuaQ":
        raise ParseError("not a Lua 5.1 / HKS chunk")
    r.p = 5
    fmt, endian, isz, ssz, insz, nsz, integral, flags, _pad = (r.u8() for _ in range(9))
    if (endian, isz, ssz, insz, nsz) != (0, 4, 8, 4, 4):
        raise ParseError(f"unexpected sizes {(endian, isz, ssz, insz, nsz)}")
    ntypes = r.u32()
    types = {}
    for _ in range(ntypes):
        tid = r.u32()
        n = r.u32()
        types[tid] = r.d[r.p:r.p + n - 1].decode()
        r.p += n
    return {"format": fmt, "flags": flags, "integral": integral, "types": types}


def parse_func(r: Reader) -> Func:
    off = r.p
    upvals = r.u32()
    params = r.u32()
    vararg = r.u8()
    framesize = r.u32()
    r.u32()  # unknown, zero in every function seen
    ninstr = r.u32()
    if ninstr > 1 << 20:
        raise ParseError(f"implausible instruction count {ninstr} at {off:#x}")
    r.p = (r.p + 3) & ~3
    instrs = [Instr(v) for v in struct.unpack_from(f">{ninstr}I", r.d, r.p)]
    r.p += 4 * ninstr
    nconst = r.u32()
    consts: list = []
    for _ in range(nconst):
        t = r.u8()
        if t == 0:
            consts.append(None)
        elif t == 1:
            consts.append(bool(r.u8()))
        elif t == 3:
            consts.append(r.f32())
        elif t == 4:
            s = r.string()
            consts.append(_Str(s if s is not None else ""))
        elif t in (2, 11):
            consts.append(("ui64", r.u64()))
        else:
            raise ParseError(f"unknown constant type {t} at {r.p - 1:#x}")
    f = Func(off, upvals, params, vararg, framesize, instrs, consts)
    hasdebug = r.u32()
    if hasdebug not in (0, 1):
        raise ParseError(f"debug flag {hasdebug} at {r.p - 4:#x}")
    if hasdebug:
        nlines = r.u32()
        nlocals = r.u32()
        nup = r.u32()
        r.u32()
        r.u32()
        f.debug_path = r.string()
        f.debug_name = r.string()
        f.lines = [r.u32() for _ in range(nlines)]
        for _ in range(nlocals):
            f.locals.append(r.string() or "")
            r.u32()
            r.u32()
        for _ in range(nup):
            r.string()
    nchild = r.u32()
    if nchild > 1 << 16:
        raise ParseError(f"implausible child count {nchild} at {r.p - 4:#x}")
    for _ in range(nchild):
        c = parse_func(r)
        c.parent = f
        f.children.append(c)
    return f


class _Str(str):
    """A string constant, distinguished from names this tool derives."""


def parse_file(path: str):
    data = open(path, "rb").read()
    r = Reader(data)
    hdr = parse_header(r)
    main = parse_func(r)
    # Every chunk ends in a 12-byte tail after the main prototype; in c0000.hks
    # it is u32 1 then eight zero bytes.  Its meaning is not established, so it
    # is returned raw and the selftest pins its shape across all scripts.
    return data, r.p, hdr, main


def walk(f: Func):
    yield f
    for c in f.children:
        yield from walk(c)


def rk(f: Func, v: int) -> str:
    """Render a 9-bit RK operand (C field) of HKS: bit 8 set means constant."""
    if v & 0x100:
        return kstr(f, v & 0xFF)
    return f"R{v}"


def kstr(f: Func, i: int) -> str:
    if i >= len(f.consts):
        return f"K{i}?"
    v = f.consts[i]
    if isinstance(v, _Str):
        return repr(str(v))
    if isinstance(v, float):
        return f"{v:g}"
    return repr(v)


def name_functions(main: Func) -> None:
    """Name each prototype by the global/field it is stored into after `CLOSURE`."""
    main.name = "<main>"
    for i, f in enumerate(walk(main)):
        f.index = i
    for f in walk(main):
        for pc, ins in enumerate(f.instrs):
            if ins.op != OP["CLOSURE"]:
                continue
            child = f.children[ins.bx] if ins.bx < len(f.children) else None
            if child is None:
                continue
            dest = None
            for nxt in f.instrs[pc + 1:pc + 1 + max(child.upvals, 0) + 3]:
                if nxt.op == OP["SETGLOBAL"] and nxt.a == ins.a:
                    dest = str(f.consts[nxt.bx])
                    break
                if nxt.op in (OP["SETFIELD"], OP["SETFIELD_R1"]) and (nxt.c & 0xFF) == ins.a:
                    dest = f"{f.name}.{f.consts[nxt.b]}" if nxt.b < len(f.consts) else None
                    break
            child.name = dest or child.debug_name or f"{f.name}/closure{ins.bx}"
    # anything left unnamed: use debug name or a positional name
    for f in walk(main):
        if f.name == "?":
            f.name = f.debug_name or f"{f.parent.name}/fn{f.parent.children.index(f)}"


def disasm_line(f: Func, pc: int) -> str:
    ins = f.instrs[pc]
    op = ins.name
    a, b, c, bx, sbx = ins.a, ins.b, ins.c, ins.bx, ins.sbx
    if op in ("LOADK",):
        arg = f"R{a} {kstr(f, bx)}"
    elif op in ("GETGLOBAL", "SETGLOBAL", "GETGLOBAL_MEM"):
        arg = f"R{a} {kstr(f, bx)}"
    elif op in ("GETFIELD", "GETFIELD_R1"):
        arg = f"R{a} R{b} {kstr(f, c & 0xFF)}"
    elif op in ("SETFIELD", "SETFIELD_R1"):
        arg = f"R{a}.{kstr(f, b)} = {rk(f, c)}"
    elif op in ("JMP", "FORPREP", "FORLOOP"):
        arg = f"R{a} -> {pc + 1 + sbx}"
    elif op == "CLOSURE":
        ch = f.children[bx] if bx < len(f.children) else None
        arg = f"R{a} fn{bx} ({ch.name if ch else '?'})"
    elif op in ("MOVE", "NOT", "UNM", "LEN", "NOT_R1"):
        arg = f"R{a} R{b}"
    elif op in ("GETUPVAL", "SETUPVAL", "SETUPVAL_R1"):
        arg = f"R{a} U{b}"
    else:
        arg = f"A={a} B={b} C={rk(f, c)}"
    line = f.lines[pc] if pc < len(f.lines) else ""
    return f"{pc:5d} [{line}] {op:<14} {arg}"


# --- symbolic register tracking --------------------------------------------

#
# Values are recovered with a reaching-definitions walk over the control-flow
# graph: from a use, walk predecessors backwards until an instruction that
# writes the register.  Every definition that can reach the use is reported,
# so `x = 240; if a then x = 180 elseif b then x = 90 end; f(x)` yields all
# three values rather than whichever one comes last in program order.

COND_OPS = {"EQ", "EQ_BK", "LT", "LT_BK", "LE", "LE_BK", "TEST", "TEST_R1",
            "TESTSET", "TFORLOOP"}
END_OPS = {"RETURN", "TAILCALL", "TAILCALL_I", "TAILCALL_C", "TAILCALL_M",
           "TAILCALL_I_R1"}
WRITE_A = {"LOADK", "GETGLOBAL", "GETGLOBAL_MEM", "GETFIELD", "GETFIELD_R1",
           "GETFIELD_MM", "MOVE", "LOADBOOL", "GETUPVAL", "GETTABLE",
           "GETTABLE_S", "GETTABLE_N", "ADD", "ADD_BK", "SUB", "SUB_BK", "MUL",
           "MUL_BK", "DIV", "DIV_BK", "MOD", "MOD_BK", "POW", "POW_BK", "UNM",
           "NOT", "NOT_R1", "LEN", "CONCAT", "NEWTABLE", "CLOSURE", "TESTSET",
           "GETSLOT", "GETSLOTMT", "GETSLOT_D", "NEWSTRUCT",
           "INTRINSIC_INDEX", "INTRINSIC_INDEX_LITERAL"}


def successors(f: Func, pc: int) -> list[int]:
    ins = f.instrs[pc]
    n = ins.name
    if n in END_OPS:
        return []
    if n in ("JMP", "FORPREP"):
        return [pc + 1 + ins.sbx]
    if n == "FORLOOP":
        return [pc + 1 + ins.sbx, pc + 1]
    if n in COND_OPS:
        return [pc + 1, pc + 2]
    if n == "LOADBOOL" and ins.c:
        return [pc + 2]
    return [pc + 1]


def predecessors(f: Func) -> list[list[int]]:
    cached = getattr(f, "_preds", None)
    if cached is not None:
        return cached
    preds: list[list[int]] = [[] for _ in f.instrs]
    for pc in range(len(f.instrs)):
        for s in successors(f, pc):
            if 0 <= s < len(f.instrs):
                preds[s].append(pc)
    f._preds = preds  # type: ignore[attr-defined]
    return preds


def writes(f: Func, pc: int, reg: int) -> bool:
    ins = f.instrs[pc]
    n = ins.name
    a = ins.a
    if n in WRITE_A:
        return reg == a
    if ins.op in CALL_OPS:
        if ins.c == 0:
            return reg >= a
        return a <= reg <= a + ins.c - 2
    if n == "SELF":
        return reg in (a, a + 1)
    if n == "LOADNIL":
        return a <= reg <= ins.b
    if n in ("FORPREP", "FORLOOP"):
        return a <= reg <= a + 3
    if n == "TFORLOOP":
        return a + 3 <= reg <= a + 2 + ins.c
    if n == "VARARG":
        return reg >= a if ins.b == 0 else a <= reg <= a + ins.b - 2
    return False


def reaching_defs(f: Func, pc: int, reg: int) -> list[int]:
    """Instruction indexes whose write to `reg` can reach `pc`; -1 is function entry."""
    preds = predecessors(f)
    found: list[int] = []
    seen: set[int] = set()
    stack = list(preds[pc])
    if pc == 0:
        found.append(-1)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        if writes(f, node, reg):
            found.append(node)
            continue
        if node == 0:
            found.append(-1)
        stack.extend(preds[node])
    return sorted(set(found))


def value_of(f: Func, pc: int, reg: int, depth: int = 0) -> list[str]:
    """Every expression `reg` can hold just before `pc`."""
    if depth > 6:
        return ["..."]
    out: list[str] = []
    for d in reaching_defs(f, pc, reg):
        for v in def_expr(f, d, reg, depth + 1):
            if v not in out:
                out.append(v)
    return out or [f"R{reg}?"]


def alt(vals: list[str]) -> str:
    return vals[0] if len(vals) == 1 else "{" + " | ".join(vals) + "}"


def def_expr(f: Func, d: int, reg: int, depth: int) -> list[str]:
    if d < 0:
        return [f"param{reg}" if reg < f.params else "nil(entry)"]
    ins = f.instrs[d]
    n = ins.name
    if n == "LOADK":
        return [kstr(f, ins.bx)]
    if n in ("GETGLOBAL", "GETGLOBAL_MEM"):
        return [str(f.consts[ins.bx])]
    if n in ("GETFIELD", "GETFIELD_R1"):
        return [f"{alt(value_of(f, d, ins.b, depth))}.{f.consts[ins.c & 0xFF]}"]
    if n == "MOVE":
        return value_of(f, d, ins.b, depth)
    if n == "LOADBOOL":
        return ["true" if ins.b else "false"]
    if n == "LOADNIL":
        return ["nil"]
    if n == "GETUPVAL":
        return [f"upval{ins.b}"]
    if n == "UNM":
        return [f"-{alt(value_of(f, d, ins.b, depth))}"]
    if n in ("ADD", "ADD_BK", "SUB", "SUB_BK", "MUL", "MUL_BK", "DIV", "DIV_BK"):
        sym = {"ADD": "+", "SUB": "-", "MUL": "*", "DIV": "/"}[n.split("_")[0]]
        lhs = kstr(f, ins.b) if n.endswith("_BK") else alt(value_of(f, d, ins.b, depth))
        rhs = kstr(f, ins.c & 0xFF) if ins.c & 0x100 else alt(value_of(f, d, ins.c, depth))
        return [f"({lhs} {sym} {rhs})"]
    if ins.op in CALL_OPS:
        callee, args = call_at(f, d, depth)
        text = f"{callee}({', '.join(args)})"
        return [text if reg == ins.a else f"{text}#ret{reg - ins.a}"]
    return [f"<{n}@{d}>"]


def call_at(f: Func, pc: int, depth: int = 0) -> tuple[str, list[str]]:
    """Callee and argument expressions of the call at `pc`."""
    ins = f.instrs[pc]
    base = ins.a
    callee = alt(value_of(f, pc, base, depth))
    if ins.b:
        last = base + ins.b - 1
    else:
        # B == 0: the arguments run to the top left by a multi-result call
        last = base
        for back in range(pc - 1, -1, -1):
            prev = f.instrs[back]
            if prev.op in CALL_OPS:
                if prev.c == 0:
                    inner, iargs = call_at(f, back, depth + 1)
                    fixed = [alt(value_of(f, pc, r, depth)) for r in range(base + 1, prev.a)]
                    return callee, fixed + [f"{inner}({', '.join(iargs)})..."]
                break
    return callee, [alt(value_of(f, pc, r, depth)) for r in range(base + 1, last + 1)]


def calls(f: Func):
    """Yield (pc, callee_expr, [arg_exprs]) for every call in `f`."""
    for pc, ins in enumerate(f.instrs):
        if ins.op in CALL_OPS:
            callee, args = call_at(f, pc)
            yield pc, callee, args


# HksActId value of SetTurnSpeed, read out of the script's own wrapper
# `SetTurnSpeed(turn_speed) act(2004, turn_speed) end` (see `--dump SetTurnSpeed`).
TURN_SPEED_ACT = "2004"


def reaching_callers(main: Func, target: str) -> dict[str, list[str]]:
    """Every named prototype that can reach a call to `target`, with one call path.

    Edges are calls whose callee resolves to a global name; a callee held in a
    table field or computed at runtime is not followed, which the caller must
    account for (`--turn-speed` also reports every `act(2004, ...)` directly).
    """
    edges: dict[str, set[str]] = {}
    for f in walk(main):
        for _pc, callee, _args in calls(f):
            edges.setdefault(f.name, set()).add(callee)
    paths: dict[str, list[str]] = {target: [target]}
    changed = True
    while changed:
        changed = False
        for name, outs in edges.items():
            if name in paths:
                continue
            for o in sorted(outs):
                if o in paths:
                    paths[name] = [name] + paths[o]
                    changed = True
                    break
    paths.pop(target)
    return paths


def turn_speed_report(main: Func) -> list[tuple]:
    rows = []
    for f in walk(main):
        for pc, callee, args in calls(f):
            if callee == "act" and args and args[0] == TURN_SPEED_ACT:
                rows.append((f, pc, callee, args))
            elif callee in ("SetTurnSpeed", "SetNpcTurnSpeed", "SetRollingTurnCondition"):
                rows.append((f, pc, callee, args))
    return rows


# --- selftest ---------------------------------------------------------------

def selftest(path: str) -> int:
    fails = 0

    def check(cond: bool, what: str) -> None:
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + what)
        if not cond:
            fails += 1

    data, end, hdr, main = parse_file(path)
    tail = data[end:]
    check(tail == b"\x00\x00\x00\x01" + b"\x00" * 8,
          f"main prototype ends 12 bytes before EOF, tail u32 1 + 8 zero bytes ({end:#x} of {len(data):#x}, tail {tail.hex()})")
    check(hdr["types"].get(4) == "TSTRING" and hdr["types"].get(3) == "TNUMBER",
          "type table maps 3->TNUMBER, 4->TSTRING (the constant tags used)")
    funcs = list(walk(main))
    check(len(funcs) > 100, f"{len(funcs)} function prototypes parsed")
    name_functions(main)
    all_strings = {str(k) for f in funcs for k in f.consts if isinstance(k, _Str)}
    for s in ("SetTurnSpeed", "SetNpcTurnSpeed", "ExecAttack", "act", "env"):
        check(s in all_strings, f"string constant {s!r} present")
    by_name = {f.name: f for f in funcs}
    # the raw file carries `turn_speed` / `npc_turn_speed` only as debug local names
    check(by_name.get("SetTurnSpeed") is not None and by_name["SetTurnSpeed"].locals[:1] == ["turn_speed"],
          "SetTurnSpeed's first local (its parameter) is named 'turn_speed' in debug info")
    check(by_name.get("SetNpcTurnSpeed") is not None and by_name["SetNpcTurnSpeed"].locals[:1] == ["turn_speed"],
          "SetNpcTurnSpeed's first local is named 'turn_speed' in debug info")
    check(by_name.get("Move_onUpdate") is not None and by_name["Move_onUpdate"].locals[1:2] == ["npc_turn_speed"],
          "Move_onUpdate's second local is named 'npc_turn_speed' in debug info")
    wrapper = [a for pc, c, a in calls(by_name["SetTurnSpeed"]) if c == "act"]
    check(wrapper == [[TURN_SPEED_ACT, "param0"]], f"SetTurnSpeed wrapper is act({TURN_SPEED_ACT}, param0): {wrapper}")
    # independent: in the 1.16.2 image, HksAct's switch subtracts 2002 and entry 2
    # of its table is the case that stores to behaviorData+0x250
    deobf = os.environ.get("ER_DEOBF_BIN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eldenring-deobf.bin"))
    if os.path.exists(deobf):
        with open(deobf, "rb") as fh:
            fh.seek(0x40E2E8)
            table = struct.unpack("<3I", fh.read(12))
            fh.seek(0x40D57A)
            sub = fh.read(6)
            fh.seek(0x40D9E1)
            store = fh.read(8)
        check(sub == bytes.fromhex("81c22ef8ffff") and table[2] == 0x40D9BA
              and store == bytes.fromhex("f30f118350020000"),
              "eldenring-deobf.bin: HksAct `add edx,-2002`, table[2] -> 0x40d9ba, "
              "which stores `movss [rbx+0x250]` -- act id 2004 is SetTurnSpeed")
    else:
        print(f"SKIP deobf cross-check ({deobf} absent)")
    # the same layout must parse every sibling script to the same 12-byte tail
    sib_dir = os.path.dirname(os.path.abspath(path))
    sibs = sorted(p for p in os.listdir(sib_dir) if p.endswith(".hks"))
    sib_bad = []
    for p in sibs:
        try:
            d2, e2, _h, _m = parse_file(os.path.join(sib_dir, p))
            if len(d2) - e2 != 12:
                sib_bad.append(f"{p}: tail {len(d2) - e2}")
        except (ParseError, UnicodeDecodeError, struct.error) as exc:
            sib_bad.append(f"{p}: {exc}")
    check(not sib_bad, f"all {len(sibs)} .hks files beside it parse to a 12-byte tail ({sib_bad[:3]})")
    # every Bx/Kx constant index used by LOADK/GETGLOBAL/SETGLOBAL is in range
    bad = sum(1 for f in funcs for i in f.instrs
              if i.name in ("LOADK", "GETGLOBAL", "SETGLOBAL") and i.bx >= len(f.consts))
    check(bad == 0, f"all LOADK/GETGLOBAL/SETGLOBAL constant indexes in range ({bad} bad)")
    # SETGLOBAL / GETGLOBAL name operands are strings
    nonstr = sum(1 for f in funcs for i in f.instrs
                 if i.name in ("GETGLOBAL", "SETGLOBAL") and i.bx < len(f.consts)
                 and not isinstance(f.consts[i.bx], _Str))
    check(nonstr == 0, f"every GETGLOBAL/SETGLOBAL operand is a string constant ({nonstr} not)")
    unknown = sum(1 for f in funcs for i in f.instrs if i.op >= len(OPNAMES))
    check(unknown == 0, f"no opcode beyond the known table ({unknown} unknown)")
    # every `CLOSURE` refers to an existing child, and every child is referenced
    closure_ok = all(i.bx < len(f.children) for f in funcs for i in f.instrs if i.name == "CLOSURE")
    check(closure_ok, "every CLOSURE index names an existing child prototype")
    refd = sum(len({i.bx for i in f.instrs if i.name == "CLOSURE"}) for f in funcs)
    check(refd == len(funcs) - 1, f"CLOSUREs reference every child exactly ({refd} of {len(funcs) - 1})")
    # line tables, when present, match instruction count
    mism = sum(1 for f in funcs if f.lines and len(f.lines) != len(f.instrs))
    check(mism == 0, f"debug line tables match instruction counts ({mism} mismatched)")
    # every function ends with `RETURN`
    noret = sum(1 for f in funcs if not f.instrs or f.instrs[-1].name != "RETURN")
    check(noret == 0, f"every prototype ends in RETURN ({noret} do not)")
    print("selftest:", "OK" if fails == 0 else f"{fails} FAILED")
    return 1 if fails else 0


def main_cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", default=DEFAULT_FILE)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true", help="list every prototype")
    ap.add_argument("--dump", metavar="NAME", action="append", help="disassemble a prototype by name")
    ap.add_argument("--turn-speed", action="store_true", help="report SetTurnSpeed calls")
    ap.add_argument("--calls-to", metavar="NAME", help="report every call whose callee is NAME")
    ap.add_argument("--const", metavar="STR", help="list functions using string constant STR")
    ap.add_argument("--reaches", metavar="NAME", help="list prototypes whose calls can reach NAME")
    args = ap.parse_args()
    if args.selftest:
        return selftest(args.file)
    _data, _end, _hdr, main = parse_file(args.file)
    name_functions(main)
    if args.list:
        for f in walk(main):
            print(f"{f.index:5d} {f.name}  params={f.params} instrs={len(f.instrs)} consts={len(f.consts)}")
    for n in args.dump or []:
        for f in walk(main):
            if f.name == n:
                print(f"== {f.name} (#{f.index} @ {f.offset:#x}) params={f.params} upvals={f.upvals}")
                for pc in range(len(f.instrs)):
                    print(disasm_line(f, pc))
    if args.calls_to:
        for f in walk(main):
            for pc, callee, a in calls(f):
                if callee == args.calls_to:
                    print(f"{f.name} pc={pc}: {callee}({', '.join(a)})")
    if args.const:
        for f in walk(main):
            if any(isinstance(k, _Str) and str(k) == args.const for k in f.consts):
                print(f.name)
    if args.reaches:
        for name, path in sorted(reaching_callers(main, args.reaches).items()):
            print(f"{name}: {' -> '.join(path)}")
    if args.turn_speed:
        for f, pc, callee, a in turn_speed_report(main):
            line = f.lines[pc] if pc < len(f.lines) else "?"
            print(f"{f.name}  pc={pc} line={line}: {callee}({', '.join(a)})")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
