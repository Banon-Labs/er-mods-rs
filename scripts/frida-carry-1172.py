#!/usr/bin/env python3
"""Carry the game addresses written into a Frida agent or a live Python tool to 2.7.2.0.

Run it under uv, which provides capstone and numpy:

    uv run --with capstone --with numpy python3 scripts/frida-carry-1172.py scripts/frida/x.js
    uv run --with capstone --with numpy python3 scripts/frida-carry-1172.py --write \\
        --out-root .worktrees/er-1172-readiness scripts/frida/x.js
    uv run --with capstone --with numpy python3 scripts/frida-carry-1172.py --selftest

What it does to one file
------------------------
1. Finds every game address written in code: a `0x14xxxxxxx` literal anywhere outside a comment,
   and an rva literal (`0x3d69ff8`) where the file adds it to the game module's base -- a name that
   says rva, a `<game>.add(...)` argument, a helper wrapping one, or a constant later passed to
   either. A literal added to `ersc.dll`'s or a Steam module's base is not a game address and is
   left alone; Seamless did not change. Literals inside comments are history and are never moved.
2. Decides which build each was written for from the file's own comments: the build named next to
   that value in a comment, else the comment on its line, else the comment block above it, else the
   file's header, else 1.17.1. A 1.17.0 value is chained through `map-rvas-1170-to-1171.py`, a
   1.16.2 one through the recorded `docs/recon/rva-map-1162-to-1170*.tsv` rows.
3. Checks the value is something in the build it was written for -- an instruction boundary for
   code, a global some instruction reads for data -- so a stale 1.16.2 value without a tag is caught
   rather than carried.
4. Carries it with the segment table behind `map-rvas-1171-to-1172.py`
   (`docs/recon/carry-1171-to-1172.tsv`) and verifies the result against the two flat images:
   * code: the instructions at both addresses, rel32 and rip-relative operands masked, must match
     over 16 to 32 bytes, and every masked operand's 1.17.1 target, carried, must be the 2.7.2.0
     instruction's target;
   * `.data`/`.rdata`: the 1.17.1 instructions that read the address are carried to 2.7.2.0, and
     the twin instructions must read the carried address. An `.rdata` constant no instruction
     reads is verified through its content, its image pointers carried. A `.data` address no
     instruction reads is not verified at all: `.data` is reordered between the builds as well
     as shifted, so a neighbour that is read vouches for nothing.
5. Respells prologue bytes written beside an address (`'4057...'`, `[0x40, 0x53, ...]`) when
   they match the 1.17.1 bytes there and the 2.7.2.0 bytes differ.

Anything that fails a step is reported, and with `--write` a `.js` agent gets a `throw` at load
naming it (a `.py` tool is left for its author: where to raise depends on the tool). A verified
literal is rewritten and its old value appended to the comment on its line. A file that already
names this tool is refused, so a second run cannot carry a carried value again.

Struct offsets are out of scope: nothing here can tell a field offset from any other number.
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import io
import json
import re
import struct
import subprocess
import sys
import textwrap
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_BASE = 0x140000000
TOOL = "frida-carry-1172"
SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent

sys.path.insert(0, str(SCRIPTS))
import function_extent  # noqa: E402 - repo-local; the sys.path line above is what makes it work


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M1172 = _load("map_rvas_1171_to_1172", "map-rvas-1171-to-1172.py")
M1170 = _load("map_rvas_1170_to_1171", "map-rvas-1170-to-1171.py")

IMAGE_1170 = M1170.DEFAULT_OLD
IMAGE_1171 = M1172.OLD_IMAGE
IMAGE_1172 = M1172.NEW_IMAGE

# The 1.17.0 -> 1.17.1 step moved code at or above this rva by +0x70 and nothing else.
BOUNDARY_1170 = M1170.BOUNDARY_RVA

BUILD_TAG = re.compile(
    r"(?<![\w.])(1\.16\.2|1\.17\.[012]|2\.7\.[012]\.0|1\.17)(?![\w.]*\d)"
)
TAG_NAMES = {"2.7.0.0": "1.17.0", "2.7.1.0": "1.17.1", "2.7.2.0": "1.17.2"}
HEX = re.compile(r"\b0[xX][0-9a-fA-F]+\b")
FOREIGN = re.compile(r"ersc|seamless|steam|ntdll|kernel|xinput|dinput|d3d|dxgi|vulkan|winmm|user32",
                     re.IGNORECASE)


# ----------------------------------------------------------------------------------------------
# images


class Images:
    """The three flat de-Arxan'd images, loaded on first use."""

    def __init__(self):
        self._img = {}
        self._pdata = {}
        self._readers = None
        self.rows = M1172.read_tsv()
        self._starts = [r[1] for r in self.rows]
        import capstone

        self.cs = capstone
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        self.md.detail = True
        self.lite = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)

    def image(self, build: str):
        if build not in self._img:
            path = {"1.17.0": IMAGE_1170, "1.17.1": IMAGE_1171, "1.17.2": IMAGE_1172}[build]
            self._img[build] = M1172.Image(path)
        return self._img[build]

    def pdata(self, build: str):
        if build not in self._pdata:
            entries = self.image(build).pdata()
            self._pdata[build] = (entries, [b for b, _ in entries], sorted(e for _, e in entries))
        return self._pdata[build]

    def section(self, build: str, rva: int) -> str | None:
        for name, lo, size in self.image(build).sections:
            if lo <= rva < lo + max(size, 1):
                return name
        return None

    def carry(self, rva: int) -> int | None:
        at = bisect.bisect_right(self._starts, rva) - 1
        if at >= 0 and self.rows[at][1] <= rva < self.rows[at][2]:
            return rva + self.rows[at][3]
        return None

    # -- instructions --------------------------------------------------------------------------

    def insn(self, build: str, rva: int):
        data = self.image(build).data
        for i in self.md.disasm(data[rva:rva + 16], rva, 1):
            return i
        return None

    def rip_target(self, i) -> int | None:
        cs = self.cs
        for op in i.operands:
            if op.type == cs.x86.X86_OP_MEM and op.mem.base == cs.x86.X86_REG_RIP:
                return i.address + i.size + op.mem.disp
        return None

    def branch_target(self, i) -> int | None:
        cs = self.cs
        if i.group(cs.CS_GRP_BRANCH_RELATIVE) and i.operands and i.operands[0].type == cs.x86.X86_OP_IMM:
            return i.operands[0].imm
        return None

    def is_boundary(self, build: str, rva: int) -> tuple[bool, str]:
        """Whether rva starts an instruction, decoding forward from the function around it."""
        entries, begins, ends = self.pdata(build)
        at = bisect.bisect_right(begins, rva) - 1
        start = None
        if at >= 0 and entries[at][0] <= rva < entries[at][1]:
            start = entries[at][0]
            if start == rva:
                return True, "function entry"
        else:
            # A leaf: .pdata does not describe it. MSVC starts functions on 16 bytes, and what sits
            # before one is filler, a `ret`, a `jmp` or the debris of a neutralised Arxan stub, so
            # decoding forward across the gap proves nothing either way. An aligned leaf start is
            # taken as one; the byte compare against 2.7.2.0 still has to pass.
            if rva % 16 == 0:
                return True, "aligned leaf entry"
            j = bisect.bisect_right(ends, rva) - 1
            if j < 0:
                return False, "before any function"
            start = ends[j]
            data = self.image(build).data
            while start < rva and data[start] in (0xCC, 0x90):
                start += 1
            if start == rva:
                return True, "leaf entry after filler"
        data = self.image(build).data
        for address, _size, _m, _o in self.lite.disasm_lite(data[start:rva + 16], start):
            if address == rva:
                return True, "instruction boundary"
            if address > rva:
                break
        return False, f"not an instruction boundary (decoding from {start:#x} steps over it)"

    def is_entry(self, build: str, rva: int) -> bool:
        entries, begins, _ = self.pdata(build)
        at = bisect.bisect_left(begins, rva)
        return at < len(begins) and begins[at] == rva

    def compare_code(self, old: int, new: int) -> tuple[bool, str]:
        """Masked instruction compare of 1.17.1 at old with 2.7.2.0 at new."""
        cs = self.cs
        od, nd = self.image("1.17.1").data, self.image("1.17.2").data
        compared = 0
        notes = []
        o, n = old, new
        while compared < 16:
            a = self.insn("1.17.1", o)
            b = self.insn("1.17.2", n)
            if a is None:
                return False, f"1.17.1 bytes at {o:#x} do not decode"
            if b is None:
                return False, f"2.7.2.0 bytes at {n:#x} do not decode"
            if a.size != b.size or a.mnemonic != b.mnemonic:
                return False, f"+{o - old:#x}: {a.mnemonic} {a.op_str} vs {b.mnemonic} {b.op_str}"
            mask = bytearray(a.size)
            ta = tb = None
            if self.rip_target(a) is not None:
                for k in range(a.disp_offset, a.disp_offset + a.disp_size):
                    mask[k] = 1
                ta, tb = self.rip_target(a), self.rip_target(b)
            elif self.branch_target(a) is not None:
                for k in range(a.imm_offset, a.imm_offset + a.imm_size):
                    mask[k] = 1
                ta, tb = self.branch_target(a), self.branch_target(b)
            ab, bb = od[o:o + a.size], nd[n:n + b.size]
            if any(x != y and not m for x, y, m in zip(ab, bb, mask)):
                return False, f"+{o - old:#x}: {a.mnemonic} {a.op_str} differs in unmasked bytes"
            if ta is not None:
                if tb is None:
                    return False, f"+{o - old:#x}: operand kind differs"
                want = self.carry(ta)
                if want is None:
                    notes.append(f"operand {ta:#x} not carried")
                elif want != tb:
                    return False, (f"+{o - old:#x}: {a.mnemonic} target {ta:#x} carries to {want:#x}, "
                                   f"2.7.2.0 has {tb:#x}")
            compared += a.size
            o += a.size
            n += b.size
            if a.mnemonic in ("ret", "int3", "jmp", "ud2") or compared >= 32:
                break
        why = f"{compared} bytes match, operands masked"
        if notes:
            why += "; " + ", ".join(notes)
        return True, why

    # -- data readers ---------------------------------------------------------------------------

    def _reader_index(self):
        if self._readers is None:
            import numpy as np

            img = self.image("1.17.1")
            od = np.frombuffer(img.data, dtype=np.uint8)
            p = np.arange(img.text_lo + 1, img.text_hi - 4, dtype=np.int64)
            p = p[(od[p - 1] & 0xC7) == 0x05]
            b = od[p[:, None] + np.arange(4)].astype(np.uint32)
            v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16) | (b[:, 3] << 24)
            disp = v.astype(np.int64) - ((v >= 0x80000000).astype(np.int64) << 32)
            self._readers = (p, p + 4 + disp)
        return self._readers

    def readers(self, target: int, limit: int = 64) -> list[int]:
        """1.17.1 instruction addresses with a rip-relative operand pointing at target."""
        import numpy as np

        p, base = self._reader_index()
        found = []
        for k in (0, 1, 2, 4):
            for disp_at in p[np.nonzero(base == target - k)[0]].tolist():
                for back in range(2, 9):
                    s = disp_at - back
                    i = self.insn("1.17.1", s)
                    if i is None or i.disp_offset != back or i.size < back + 4:
                        continue
                    if self.rip_target(i) == target:
                        found.append(s)
                        break
                if len(found) >= limit:
                    return sorted(set(found))
        return sorted(set(found))

    def verify_readers(self, old: int, new: int, explain: bool = False) -> tuple[int, int, int, list[str]]:
        """(agree, disagree, uncounted, examples) over the 1.17.1 readers of old, carried.

        A reader whose carried twin is a different instruction is not counted either way: Arxan
        re-randomises inline obfuscation per build, so about a tenth of `.text` differs between the
        two de-Arxan'd images while still being the same function, and the carry of an address in
        such a stretch says nothing about the global. Only the same instruction reading another
        address is a disagreement.
        """
        agree = disagree = uncounted = 0
        examples = []
        for r in self.readers(old):
            r2 = self.carry(r)
            a = self.insn("1.17.1", r)
            b = self.insn("1.17.2", r2) if r2 is not None else None
            t = self.rip_target(b) if b is not None else None
            if explain:
                print(f"    reader {IMAGE_BASE + r:#x} {a.mnemonic} {a.op_str}  ->  "
                      f"{'-' if r2 is None else hex(IMAGE_BASE + r2)} "
                      f"{'' if b is None else b.mnemonic + ' ' + b.op_str}  target "
                      f"{'-' if t is None else hex(IMAGE_BASE + t)}")
            if b is None:
                uncounted += 1
            elif t == new:
                agree += 1
                if len(examples) < 2:
                    examples.append(f"{IMAGE_BASE + r2:#x} {b.mnemonic} {b.op_str}")
            elif b.mnemonic == a.mnemonic and b.size == a.size and t is not None:
                disagree += 1
            else:
                uncounted += 1
        return agree, disagree, uncounted, examples

    def verify_data(self, old: int, new: int, sec: str, explain: bool = False) -> tuple[bool, str]:
        agree, disagree, uncarried, ex = self.verify_readers(old, new, explain)
        if agree and agree >= 9 * disagree:
            why = f"{agree} carried readers read it" + (f", {disagree} disagree" if disagree else "") \
                + (f", {uncarried} land on other code" if uncarried else "")
            return True, why + (f" (e.g. {ex[0]})" if ex else "")
        if agree or disagree:
            return False, f"carried readers split {agree} agree / {disagree} disagree"
        # No reader of its own. A read address just below it does not vouch for it: `.data` is
        # reordered between the builds as well as shifted (`0x3d6deb4` moved `+0x301c` while its
        # neighbours moved `+0x3024`), so a neighbour's delta says nothing about this one.
        if sec == ".rdata":
            od, nd = self.image("1.17.1").data, self.image("1.17.2").data
            a, b = od[old:old + 32], nd[new:new + 32]
            if any(a):
                ok = True
                for q in range(0, 32, 8):
                    x = int.from_bytes(a[q:q + 8], "little")
                    y = int.from_bytes(b[q:q + 8], "little")
                    if IMAGE_BASE <= x < IMAGE_BASE + len(od):
                        c = self.carry(x - IMAGE_BASE)
                        ok &= c is not None and c + IMAGE_BASE == y
                    else:
                        ok &= a[q:q + 8] == b[q:q + 8]
                if ok:
                    return True, "no reader; 32 bytes of content match, image pointers carried"
        return False, f"no 1.17.1 reader carries ({uncarried} readers land on other code)" if uncarried \
            else "no 1.17.1 instruction reads it or anything just below it"

    # -- struct offsets ---------------------------------------------------------------------------
    #
    # Not used by the literal carry: nothing in a file says which number is a field offset. These
    # answer the question by hand, for one chain at a time (`--witness`, `--getter`).

    _FAMILY = {}

    def _family(self, reg_name: str) -> str:
        """`eax`, `ax`, `al` -> `rax`; `r8d` -> `r8`."""
        n = reg_name
        m = re.fullmatch(r"r(\d+)[dwb]?", n)
        if m:
            return f"r{m.group(1)}"
        for full, parts in (("rax", "eax ax al ah"), ("rbx", "ebx bx bl bh"), ("rcx", "ecx cx cl ch"),
                            ("rdx", "edx dx dl dh"), ("rsi", "esi si sil"), ("rdi", "edi di dil"),
                            ("rbp", "ebp bp bpl"), ("rsp", "esp sp spl")):
            if n == full or n in parts.split():
                return full
        return n

    def chain_sites(self, build: str, starts: list[int], offsets: list[int], window: int = 12):
        """Instructions that follow `reg = [global]` through `[reg + off]` for each offset in turn.

        `starts` are the instructions that load the global. Returns (start, address of the
        instruction that applied the last offset) for each start whose chain completed in the window.
        """
        cs = self.cs
        data = self.image(build).data
        out = []
        for s in starts:
            first = self.insn(build, s)
            if first is None or not first.operands or first.operands[0].type != cs.x86.X86_OP_REG:
                continue
            reg = self._family(first.reg_name(first.operands[0].reg))
            want = list(offsets)
            at = s + first.size
            # The chain is followed inside the function the load sits in and no further: past its
            # end the bytes decode into instructions nobody assembled, and a field read there is
            # one the class never had. No extent, no chain; the byte count only caps the slice.
            end = function_extent.body_slice_end(data, IMAGE_BASE + s, cap=first.size + window * 15)
            if end is None:
                continue
            for i in self.md.disasm(data[at:end], at, window):
                hit = None
                for op in i.operands:
                    if op.type == cs.x86.X86_OP_MEM and op.mem.base and op.mem.index == 0 and \
                            self._family(i.reg_name(op.mem.base)) == reg and op.mem.disp == want[0]:
                        hit = op
                if hit is not None:
                    want.pop(0)
                    if not want:
                        out.append((s, i.address))
                        break
                    if i.mnemonic in ("mov", "lea") and i.operands[0].type == cs.x86.X86_OP_REG:
                        reg = self._family(i.reg_name(i.operands[0].reg))
                        continue
                    break
                # A write to the tracked register ends the chain.
                if i.operands and i.operands[0].type == cs.x86.X86_OP_REG and \
                        self._family(i.reg_name(i.operands[0].reg)) == reg and \
                        i.mnemonic not in ("cmp", "test", "push"):
                    break
                if i.mnemonic in ("ret", "jmp", "call"):
                    break
        return out

    def witness_chain(self, global_rva_1171: int, offsets: list[int]) -> tuple[int, int, int, str]:
        """(sites in 1.17.1, of them carried to the same chain in 2.7.2.0, sites in 2.7.2.0, example).

        A chain read off the global in both images, at twin instructions, is the offset confirmed
        for the class the global holds, not merely for some class with a field at that offset.
        """
        new_global = self.carry(global_rva_1171)
        old_starts = self.readers(global_rva_1171, limit=400)
        old_sites = self.chain_sites("1.17.1", old_starts, offsets)
        carried = 0
        example = ""
        for start, _site in old_sites:
            s2 = self.carry(start)
            if s2 is None or new_global is None:
                continue
            i = self.insn("1.17.2", s2)
            if i is None or self.rip_target(i) != new_global:
                continue
            got = self.chain_sites("1.17.2", [s2], offsets)
            if got:
                carried += 1
                if not example:
                    j = self.insn("1.17.2", got[0][1])
                    example = f"{IMAGE_BASE + got[0][1]:#x} {j.mnemonic} {j.op_str}"
        return len(old_sites), carried, len(old_sites), example

    def function_offsets(self, rva_1171: int) -> tuple[bool, str, dict[int, int]]:
        """Compare a whole 1.17.1 function with its 2.7.2.0 twin and list the field offsets it uses.

        Returns (identical, how, {displacement: count}) over `[reg+disp]` operands whose base is
        not rip, rsp or rbp. When the whole body matches with only rel32 and rip-relative operands
        masked, every one of those displacements is used the same way by the same code in both
        builds -- which is as close to an accessor as a field without its own getter gets.
        """
        cs = self.cs
        entries, begins, _ = self.pdata("1.17.1")
        at = bisect.bisect_right(begins, rva_1171) - 1
        if at >= 0 and entries[at][0] <= rva_1171 < entries[at][1]:
            lo, hi = entries[at]
        else:
            lo, hi = rva_1171, rva_1171 + 0x1000
        new_lo = self.carry(lo)
        if new_lo is None:
            return False, "the function is refused by the segment table", {}
        od, nd = self.image("1.17.1").data, self.image("1.17.2").data
        offsets: dict[int, int] = {}
        o, n = lo, new_lo
        while o < hi:
            a, b = self.insn("1.17.1", o), self.insn("1.17.2", n)
            if a is None or b is None:
                return False, f"+{o - lo:#x} does not decode", offsets
            if a.size != b.size or a.mnemonic != b.mnemonic:
                return False, f"+{o - lo:#x}: {a.mnemonic} {a.op_str} vs {b.mnemonic} {b.op_str}", offsets
            mask = bytearray(a.size)
            if self.rip_target(a) is not None:
                mask[a.disp_offset:a.disp_offset + a.disp_size] = b"\x01" * a.disp_size
            elif self.branch_target(a) is not None:
                mask[a.imm_offset:a.imm_offset + a.imm_size] = b"\x01" * a.imm_size
            if any(x != y and not m for x, y, m in zip(od[o:o + a.size], nd[n:n + b.size], mask)):
                return False, f"+{o - lo:#x}: {a.mnemonic} {a.op_str} vs {b.mnemonic} {b.op_str}", offsets
            for op in a.operands:
                if op.type == cs.x86.X86_OP_MEM and op.mem.base and \
                        self._family(a.reg_name(op.mem.base)) not in ("rip", "rsp", "rbp"):
                    offsets[op.mem.disp] = offsets.get(op.mem.disp, 0) + 1
            o += a.size
            n += b.size
            if hi - lo == 0x1000 and a.mnemonic in ("ret", "int3"):
                break
        return True, f"{o - lo:#x} bytes from {IMAGE_BASE + lo:#x} identical at {IMAGE_BASE + new_lo:#x}", offsets

    def getters(self, offset: int, limit: int = 64) -> list[tuple[int, int, str]]:
        """Leaf accessors `op [rcx+offset]; ret` in 1.17.1, and their 2.7.2.0 twins when identical.

        Returns (1.17.1 rva, 2.7.2.0 rva or -1, text). A getter of some class with a field at this
        offset; the caller has to say why that class is the one the agent reads.
        """
        cs = self.cs
        od, nd = self.image("1.17.1").data, self.image("1.17.2").data
        lo, hi = self.image("1.17.1").text_lo, self.image("1.17.1").text_hi
        disps = [struct.pack("<i", offset) + b"\xc3"]
        if -0x80 <= offset < 0x80:
            disps.append(struct.pack("<b", offset) + b"\xc3")
        found = []
        for d in disps:
            at = od.find(d, lo, hi)
            while at != -1 and len(found) < limit:
                for back in range(2, 6):
                    s = at - back
                    if s % 16 and od[s - 1] not in (0xCC, 0xC3, 0x90):
                        continue
                    i = self.insn("1.17.1", s)
                    if i is None or s + i.size != at + len(d) - 1:
                        continue
                    mem = [op for op in i.operands if op.type == cs.x86.X86_OP_MEM]
                    if not mem or self._family(i.reg_name(mem[0].mem.base)) != "rcx" or \
                            mem[0].mem.disp != offset or mem[0].mem.index:
                        continue
                    n = self.carry(s)
                    same = n is not None and nd[n:n + i.size + 1] == od[s:s + i.size + 1]
                    found.append((s, n if same else -1, f"{i.mnemonic} {i.op_str}; ret"))
                    break
                at = od.find(d, at + 1, hi)
        return found


# ----------------------------------------------------------------------------------------------
# source scanning


@dataclass
class Literal:
    line: int            # 1-based
    col: int             # 0-based column of the literal text
    text: str
    value: int
    form: str            # "va" or "rva"
    basis: str           # why it was taken for a game address
    build: str = "1.17.1"
    build_from: str = "default"
    name: str = ""                 # the constant or key it is assigned to, when there is one
    rva_old: int | None = None     # the rva in 1.17.1
    kind: str = ""
    new_rva: int | None = None
    verdict: str = ""
    note: str = ""

    @property
    def old_va(self) -> int:
        return self.value if self.form == "va" else self.value + IMAGE_BASE

    def new_text(self) -> str:
        v = self.new_rva + (IMAGE_BASE if self.form == "va" else 0)
        digits = self.text[2:]
        out = f"{v:x}".zfill(len(digits))
        if any(c.isupper() for c in digits):
            out = out.upper()
        return self.text[:2] + out


def _js_comment_spans(text: str, offset: int = 0) -> list[tuple[int, int]]:
    """Comment spans of JavaScript text: line and block comments, outside strings."""
    spans = []
    i, n = 0, len(text)
    quote = None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote or (c == "\n" and quote != "`"):
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            spans.append((offset + i, offset + j))
            i = j
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            spans.append((offset + i, offset + j))
            i = j
            continue
        i += 1
    return spans


def _string_body(text: str, lo: int, hi: int) -> tuple[int, int]:
    """The offsets of a Python string token's contents, without its prefix and quotes."""
    i = lo
    while i < hi and text[i] in "rRbBuUfF":
        i += 1
    q = text[i:i + 3] if text[i:i + 3] in ('"""', "'''") else text[i:i + 1]
    return i + len(q), max(i + len(q), hi - len(q))


def _py_spans(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int, bool]]]:
    """(comment spans, string spans) of Python source, as absolute offsets.

    Docstrings are comments. Every other string may hold an embedded agent, so its own `//` and
    `/* */` comments count too. A string span carries whether it spans lines (a JavaScript body).
    """
    line_at = [0]
    for line in text.splitlines(keepends=True):
        line_at.append(line_at[-1] + len(line))

    def off(pos):
        return line_at[pos[0] - 1] + pos[1]

    fs_start = getattr(tokenize, "FSTRING_START", None)
    fs_end = getattr(tokenize, "FSTRING_END", None)
    comments, strings = [], []
    toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    # An f-string arrives as several tokens on 3.12 and later; fold each into one string token.
    folded = []
    k = 0
    while k < len(toks):
        t = toks[k]
        if fs_start is not None and t.type == fs_start:
            depth, j = 1, k + 1
            while j < len(toks) and depth:
                depth += toks[j].type == fs_start
                depth -= toks[j].type == fs_end
                j += 1
            folded.append(tokenize.TokenInfo(tokenize.STRING, "", t.start, toks[j - 1].end, t.line))
            k = j
            continue
        folded.append(t)
        k += 1
    sig = [t for t in folded if t.type not in (tokenize.NL, tokenize.COMMENT)]
    for k, t in enumerate(sig):
        if t.type != tokenize.STRING:
            continue
        prev = sig[k - 1].type if k else None
        nxt = sig[k + 1].type if k + 1 < len(sig) else None
        lo, hi = off(t.start), off(t.end)
        if prev in (None, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING) \
                and nxt in (tokenize.NEWLINE, tokenize.ENDMARKER):
            comments.append((lo, hi))
            continue
        strings.append((lo, hi, t.start[0] != t.end[0]))
        blo, bhi = _string_body(text, lo, hi)
        comments.extend(_js_comment_spans(text[blo:bhi], blo))
    for t in toks:
        if t.type == tokenize.COMMENT:
            comments.append((off(t.start), off(t.end)))
    return sorted(comments), strings


class Source:
    def __init__(self, path: Path, text: str):
        self.path = path
        self.text = text
        self.py = path.suffix == ".py"
        if self.py:
            self.comments, self.strings = _py_spans(text)
        else:
            self.comments, self.strings = _js_comment_spans(text), []
        self.lines = text.splitlines(keepends=True)
        self.line_at = [0]
        for line in self.lines:
            self.line_at.append(self.line_at[-1] + len(line))
        self._cstarts = [a for a, _ in self.comments]
        # The text with every comment blanked, so code patterns cannot match prose.
        chars = list(text)
        for a, b in self.comments:
            for k in range(a, b):
                if chars[k] != "\n":
                    chars[k] = " "
        self.code = "".join(chars)
        self._ctext: dict[int, str] = {}

    def in_comment(self, pos: int) -> bool:
        at = bisect.bisect_right(self._cstarts, pos) - 1
        return at >= 0 and self.comments[at][0] <= pos < self.comments[at][1]

    def pos_of(self, line: int, col: int) -> int:
        return self.line_at[line - 1] + col

    def line_col(self, pos: int) -> tuple[int, int]:
        line = bisect.bisect_right(self.line_at, pos)
        return line, pos - self.line_at[line - 1]

    def comment_text(self, line: int) -> str:
        """The comment part of one line."""
        if line not in self._ctext:
            lo, hi = self.line_at[line - 1], self.line_at[line]
            at = max(0, bisect.bisect_right(self._cstarts, lo) - 1)
            parts = []
            for a, b in self.comments[at:]:
                if a >= hi:
                    break
                if b > lo:
                    parts.append(self.text[max(a, lo):min(b, hi)])
            self._ctext[line] = " ".join(parts)
        return self._ctext[line]

    def line_is_comment(self, line: int) -> bool:
        lo, hi = self.line_at[line - 1], self.line_at[line]
        return self.code[lo:hi].strip() == "" and self.text[lo:hi].strip() != ""

    def in_multiline_string(self, pos: int) -> bool:
        return any(a <= pos < b and multi for a, b, multi in self.strings)


GAME_MODULE = re.compile(r"eldenring\.exe|mainModule|enumerateModules\(\)\s*\[\s*0\s*\]", re.I)
def offset_like(name: str) -> bool:
    """A name that holds a field offset, an id or a count rather than an address."""
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower().split("_")
    words = [w for w in words if w]
    if not words:
        return False
    if words[-1] in {"offset", "off", "stride", "size", "count", "span", "index", "idx", "mask", "bits",
                     "bit", "len", "length"}:
        return True
    if "main" in words and "player" in words:
        return True
    return name.isupper() and words[-1] in {"id", "ids", "row", "flag", "flags", "kind"}


def _close(code: str, at: int) -> int:
    """The offset of the bracket closing the one at `at`, in comment-blanked code."""
    pair = {"{": "}", "[": "]", "(": ")"}[code[at]]
    depth = 0
    for j in range(at, len(code)):
        if code[j] == code[at]:
            depth += 1
        elif code[j] == pair:
            depth -= 1
            if depth == 0:
                return j
    return len(code)


def _functions(code: str) -> list[tuple[str, str]]:
    """(name, body) of every named function and arrow function in the code."""
    out = []
    for m in re.finditer(r"\bfunction\s+(\w+)\s*\([^)]*\)\s*\{", code):
        out.append((m.group(1), code[m.end() - 1:_close(code, m.end() - 1) + 1]))
    for m in re.finditer(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function\s*\w*\s*\([^)]*\)|"
                         r"\([^)]*\)\s*=>|\w+\s*=>)\s*(\{)?", code):
        if m.group(2):
            out.append((m.group(1), code[m.end() - 1:_close(code, m.end() - 1) + 1]))
        else:
            end = code.find("\n", m.end())
            out.append((m.group(1), code[m.end():end if end >= 0 else len(code)]))
    return out


@dataclass
class Bases:
    game: set[str]           # variables holding the game module or its base
    game_fns: set[str]       # functions returning one
    helpers: set[str]        # functions that add an argument to the game base
    foreign: set[str]        # variables holding another module or its base
    foreign_fns: set[str]
    foreign_helpers: set[str]

    def _alt(self, names: set[str]) -> str:
        return "|".join(sorted(map(re.escape, names))) or r"(?!x)x"

    def game_add(self) -> str:
        return (rf"(?:\b(?:{self._alt(self.game)})|\b(?:{self._alt(self.game_fns)})\(\s*\)|"
                rf"eldenring\.exe['\"]\s*\))(?:\.base)?\.add\(")

    def foreign_add(self) -> str:
        return (rf"(?:\b(?:{self._alt(self.foreign)})|\b(?:{self._alt(self.foreign_fns)})\(\s*\)|"
                rf"\b\w*(?:ersc|steam|seamless)\w*|(?:ersc|steam\w*)\.dll['\"]\s*\))(?:\.base)?\.add\(")

    @property
    def any_game(self) -> bool:
        return bool(self.game or self.game_fns)


def find_bases(code: str) -> Bases:
    # Declarations and plain reassignments both: `let base = null; ... base = m.base;`.
    pairs = re.findall(r"(?:\b(?:const|let|var)\s+|^\s*|;\s*)(\w+)\s*=(?!=)\s*([^;\n]*)", code, re.M)
    fns = _functions(code)
    game, foreign = set(), set()
    game_fns = {n for n, body in fns if GAME_MODULE.search(body) and re.search(r"\breturn\b|=>", body + "=>")}
    foreign_fns = {n for n, body in fns if n not in game_fns and re.search(r"(?:ersc|steam\w*)\.dll", body, re.I)
                   and re.search(r"\breturn\b", body)}
    for name, rhs in pairs:
        if GAME_MODULE.search(rhs):
            game.add(name)
        elif FOREIGN.search(rhs) and re.search(r"Module|base|find|get", rhs):
            foreign.add(name)
    # Propagate through plain copies: `const base = game.base`, `const m = gameModule()`.
    changed = True
    while changed:
        changed = False
        for name, rhs in pairs:
            if name in game or name in foreign:
                continue
            m = re.fullmatch(r"\s*(?:await\s+)?(\w+)(\(\s*\))?(?:\.base)?\s*", rhs)
            if not m:
                continue
            head, call = m.group(1), m.group(2)
            if (head in game and not call) or (head in game_fns and call):
                game.add(name)
                changed = True
            elif (head in foreign and not call) or (head in foreign_fns and call):
                foreign.add(name)
                changed = True
    # A module name held in a constant: `const MODULE = 'ersc.dll'` then `findModuleByName(MODULE)`.
    for name, rhs in pairs:
        if re.fullmatch(r"\s*['\"](?:ersc|\w*steam\w*)\.dll['\"]\s*", rhs, re.I):
            for n2, rhs2 in pairs:
                if re.search(rf"\b{re.escape(name)}\b", rhs2) and re.search(r"Module|base|find|get", rhs2):
                    foreign.add(n2)
        if re.fullmatch(r"\s*['\"]eldenring\.exe['\"]\s*", rhs, re.I):
            game.discard(name)
            for n2, rhs2 in pairs:
                if re.search(rf"\b{re.escape(name)}\b", rhs2) and re.search(r"Module|base|find|get", rhs2):
                    game.add(n2)
    b = Bases(game, game_fns, set(), foreign, foreign_fns, set())
    for n, body in fns:
        if re.search(b.game_add(), body):
            b.helpers.add(n)
        elif re.search(b.foreign_add(), body, re.I):
            b.foreign_helpers.add(n)
    b.helpers -= game_fns
    b.foreign_helpers -= foreign_fns
    return b


def in_prose_string(text: str, line_lo: int, pos: int, end: int) -> bool:
    """Whether the literal sits inside a one-line string that holds more than words of hex."""
    line_hi = text.find("\n", pos)
    line_hi = len(text) if line_hi < 0 else line_hi
    head, tail = text[line_lo:pos], text[end:line_hi]
    for q in ("'", '"', "`"):
        # An odd count of this quote before the literal means a string of it is open.
        if head.count(q) % 2 == 1:
            inner = head[head.rfind(q) + 1:]
            if q == "`" and inner.rfind("${") > inner.rfind("}"):
                continue    # inside a template's `${...}`, which is code
            body = inner + "X" + tail[:tail.find(q)] if q in tail else None
            if body is not None and re.search(r"[A-Za-z]{3,}\s+\S|\s[A-Za-z]{3,}", body):
                return True
    return False


def _args_after(code: str, end: int) -> str:
    """The argument text of a call whose `(` ends at `end`."""
    close = _close(code, end - 1)
    return code[end:close]


def find_literals(src: Source, force: set[int], skip: set[int]) -> tuple[list[Literal], list[tuple[int, str, str]]]:
    """Game-address literals in code, and in-code literals that only might be (for review)."""
    code = src.code
    b = find_bases(code)
    # A Python tool reaching the game through `/proc/<pid>/mem` names it in a string, not a module
    # variable; so does any agent that looks the module up by name.
    has_game = b.any_game or bool(re.search(r"\b0x14[0-9a-fA-F]{7}\b|eldenring", code, re.I))
    hpat, fhpat = b._alt(b.helpers), b._alt(b.foreign_helpers)
    used_game, used_foreign = set(), set()
    for pat, into, flags in ((b.game_add(), used_game, 0), (rf"\b(?:{hpat})\(", used_game, 0),
                             (b.foreign_add(), used_foreign, re.I), (rf"\b(?:{fhpat})\(", used_foreign, 0)):
        for m in re.finditer(pat, code, flags):
            into.update(re.findall(r"[A-Za-z_]\w*", _args_after(code, m.end())))
    # A container whose elements are walked and added to a base: `for (... of Object.entries(FNS))`.
    for m in re.finditer(r"(?:Object\.(?:entries|keys|values)\(\s*(\w+)|\bof\s+(\w+)|\b(\w+)\.(?:map|forEach))", code):
        name = next(g for g in m.groups() if g)
        window = code[m.start():m.start() + 600]
        if re.search(b.game_add(), window) or re.search(rf"\b(?:{hpat})\(", window):
            used_game.add(name)
        elif re.search(b.foreign_add(), window, re.I) or re.search(rf"\b(?:{fhpat})\(", window):
            used_foreign.add(name)

    def containers(pos: int) -> list[tuple[str, str]]:
        """(container name, key) from the innermost object or array literal around pos outward."""
        out, at, key = [], pos, None
        km = re.search(r"([A-Za-z_]\w*)\s*:\s*$", code[src.line_at[src.line_col(pos)[0] - 1]:pos])
        key = km.group(1) if km else None
        for _ in range(4):
            depth = 0
            k = at - 1
            while k >= 0:
                c = code[k]
                if c in "}])":
                    depth += 1
                elif c in "{[(":
                    if depth == 0:
                        break
                    depth -= 1
                k -= 1
            if k < 0 or code[k] == "(":
                break
            lead = code[max(0, k - 80):k]
            nm = re.search(r"([A-Za-z_]\w*)\s*[:=]\s*$", lead)
            if not nm and re.search(r"\bof\s*$", lead):
                # `for (const rva of [0x8f5170, ...])`: the loop body says what the elements are.
                body = code[k:k + 600]
                tag = "of-loop-game" if re.search(b.game_add(), body) or re.search(rf"\b(?:{hpat})\(", body) \
                    else "of-loop-foreign" if re.search(b.foreign_add(), body, re.I) else ""
                out.append((tag, key or ""))
                break
            out.append((nm.group(1) if nm else "", key or ""))
            at = k
        return out

    out, review = [], []
    for m in HEX.finditer(src.text):
        pos = m.start()
        if src.in_comment(pos):
            continue
        value = int(m.group(0), 16)
        line, col = src.line_col(pos)
        before = code[src.line_at[line - 1]:pos]
        if value in skip:
            continue
        am = re.search(r"\b([A-Za-z_]\w*)\s*[:=]\s*(?:\w+\(\s*['\"]?)?$", before)
        name = am.group(1) if am else None
        if in_prose_string(src.text, src.line_at[line - 1], pos, m.end()):
            # A sentence inside a string ("1.17.0 dump 0x141f6d8d0 carried +0x70") is history
            # reported at run time, not an address the agent uses.
            continue
        if IMAGE_BASE + 0x1000 <= value < IMAGE_BASE + 0x6000000:
            out.append(Literal(line, col, m.group(0), value, "va", "image address", name=name or ""))
            continue
        if not 0x1000 <= value < 0x6000000:
            continue
        basis = None
        foreign = False
        if value in force:
            basis = "forced by --rva"
        elif re.search(b.foreign_add() + r"\s*$", before, re.I) or re.search(rf"\b(?:{fhpat})\([^()]*$", before):
            foreign = True
        elif re.search(b.game_add() + r"\s*$", before):
            basis = "added to the game base"
        elif re.search(rf"\b(?:{hpat})\([^()]*$", before):
            basis = "passed to a game-base helper"
        elif name and name in used_game and not offset_like(name) and not FOREIGN.search(name):
            # Before the foreign test: `follow(game.base.add(X))` puts X in both sets, and the
            # direct add to the game base is the stronger evidence.
            basis = f"{name} is added to the game base"
        elif name and (FOREIGN.search(name) or name in used_foreign):
            foreign = True
        elif name and re.search(r"rva", name, re.I) and has_game and not FOREIGN.search(before):
            basis = f"{name} names an rva"
        else:
            for cname, key in containers(pos):
                if cname == "of-loop-game":
                    basis = "an element of a list a loop adds to the game base"
                    break
                if FOREIGN.search(cname) or cname in used_foreign or cname == "of-loop-foreign":
                    foreign = True
                    break
                if key and offset_like(key):
                    break
                if cname and (cname in used_game or key in used_game or
                              (re.search(r"rva", cname, re.I) and has_game)):
                    basis = f"an element of {cname} ({key or 'list'}), which is added to the game base" \
                        if cname in used_game or key in used_game else f"an element of {cname}, named for rvas"
                    break
        if basis:
            out.append(Literal(line, col, m.group(0), value, "rva", basis, name=name or ""))
        elif not foreign and value >= 0x10000 and not FOREIGN.search(before):
            review.append((line, m.group(0), (name or "")))
    return out, review


def _tags(text: str) -> list[str]:
    # `eldenring-deobf-1.17.bin` is the 1.17.0 image, whatever the bare "1.17" would say.
    text = text.replace("deobf-1.17.bin", "deobf-1.17.0.bin")
    return [TAG_NAMES.get(t, t) for t in BUILD_TAG.findall(text)]


def tag_of(text: str) -> str | None:
    """The build a stretch of prose speaks for, when it does not name the value itself.

    Prose that names several builds is usually telling a history ("1.16.2 `0x7ad1c0` is `0x7ae040`
    on 1.17, the same on the installed 1.17.1"), and the code beside it holds the newest of them.
    """
    tags = set(_tags(text))
    for t in ("1.17.2", "1.17.1", "1.17.0", "1.17", "1.16.2"):
        if t in tags:
            return t
    return None


def decide_build(src: Source, lit: Literal) -> tuple[str, str]:
    """Which build a literal was written for, and where that was read."""
    va = lit.old_va
    forms = {f"{va:x}", f"{va - IMAGE_BASE:x}"}
    # 1. The value itself, named next to a build in a comment, with no other address between.
    for line in range(1, len(src.lines) + 1):
        c = src.comment_text(line)
        if not c:
            continue
        c = c.replace("deobf-1.17.bin", "deobf-1.17.0.bin")
        for h in HEX.finditer(c):
            if f"{int(h.group(0), 16):x}" not in forms:
                continue
            # "`+0x758a90` on 1.17" -- a build after the value, introduced by on/in, is its own.
            tail = c[h.end():h.end() + 30]
            nxt = HEX.search(tail)
            tail = tail[:nxt.start()] if nxt else tail
            t = re.match(r"[`'\")\]]*,?\s*(?:on|in|for)\s+(?:the\s+)?(?:installed\s+)?" + BUILD_TAG.pattern, tail)
            if t:
                return TAG_NAMES.get(t.group(1), t.group(1)), f"comment line {line} names it"
            # "1.17.1 `0x7ae040`" -- a build just before it, unless that build closes an earlier
            # phrase ("FUN_140757c40 on 1.16.2, `+0x758a90`").
            head = c[:h.start()]
            last_hex = max((x.end() for x in HEX.finditer(head)), default=-1)
            tags = [t for t in BUILD_TAG.finditer(head) if t.start() > last_hex
                    and not re.search(r"\b(?:on|in|for)\s+(?:the\s+)?(?:installed\s+)?$", head[:t.start()])]
            if tags and h.start() - tags[-1].end() <= 40:
                t = tags[-1].group(1)
                return TAG_NAMES.get(t, t), f"comment line {line} names it"
            t = BUILD_TAG.search(tail)
            if t:
                return TAG_NAMES.get(t.group(1), t.group(1)), f"comment line {line} names it"
    # 2. The comment on its own line.
    t = tag_of(src.comment_text(lit.line))
    if t:
        return t, "comment on its line"
    # 3. The comment block directly above it.
    block = []
    k = lit.line - 1
    while k >= 1 and src.line_is_comment(k) and len(block) < 30:
        block.insert(0, src.comment_text(k))
        k -= 1
    t = tag_of(" ".join(block))
    if t:
        return t, f"comment block above line {lit.line}"
    # 4. The file header.
    head = []
    for k in range(1, min(len(src.lines), 120) + 1):
        if src.line_is_comment(k):
            head.append(src.comment_text(k))
        elif src.lines[k - 1].strip() and not src.lines[k - 1].startswith(("#!", "from __future__")):
            break
    t = tag_of(" ".join(head))
    if t:
        return t, "file header"
    return "1.17.1", "default"


# ----------------------------------------------------------------------------------------------
# the carry


def _load_1162_map() -> dict[int, int]:
    """1.16.2 rva -> 1.17.0 rva, from the recorded and verified rows only."""
    out = {}
    recon = REPO_ROOT / "docs" / "recon"
    data = recon / "rva-map-1162-to-1170.data.tsv"
    if data.is_file():
        for line in data.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            f = line.split("\t")
            try:
                out[int(f[0], 16)] = int(f[1], 16)
            except (ValueError, IndexError):
                pass
    for name in ("rva-map-1162-to-1170.verified.tsv", "rva-map-1162-to-1170.needed-verified.tsv"):
        p = recon / name
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            f = line.split("\t")
            try:
                a, b = int(f[0], 16), int(f[1], 16)
            except (ValueError, IndexError):
                continue
            out[a - IMAGE_BASE if a >= IMAGE_BASE else a] = b - IMAGE_BASE if b >= IMAGE_BASE else b
    return out


_MAP_1162 = None


def _data_names_1162() -> dict[int, tuple[int, str]]:
    """1.16.2 rva -> (1.17.0 rva, constant name) for the recorded `.data`/`.rdata` rows."""
    out = {}
    p = REPO_ROOT / "docs" / "recon" / "rva-map-1162-to-1170.data.tsv"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            f = line.split("\t")
            if line.startswith("#") or len(f) < 3:
                continue
            try:
                out.setdefault(int(f[0], 16), (int(f[1], 16), f[2]))
            except ValueError:
                pass
    return out


_STOP = {"rva", "global", "va", "singleton", "ptr", "imp", "cs", "the", "of", "addr", "address", "static"}


def _name_tokens(name: str) -> set[str]:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower().split("_")
    words = [w for w in words if w and w not in _STOP]
    # `manager` and `man` are one word in this codebase's naming.
    return {"man" if w == "manager" else w for w in words}


def names_agree(a: str, b: str) -> bool:
    ta, tb = _name_tokens(a), _name_tokens(b)
    return len(ta & tb) >= 2 and (ta <= tb or tb <= ta)


def reinterpret_1162(lit: Literal) -> None:
    """An untranslated 1.16.2 global written as if current, recognised by its own name.

    `er_game_base::rva` holds 1.16.2 values by design, and copying one into an agent without the
    map is the mistake AGENTS.md describes. When the value is a recorded 1.16.2 `.data` row that the
    map moves, and the constant holding it is named like that row, it is that row.
    """
    if lit.build == "1.16.2" or lit.build_from.startswith(("comment line", "--build-of")):
        return
    rva = lit.value - IMAGE_BASE if lit.form == "va" else lit.value
    row = _data_names_1162().get(rva)
    if row and row[0] != rva and lit.name and names_agree(lit.name, row[1]):
        lit.build = "1.16.2"
        lit.build_from = f"named like the recorded 1.16.2 row {row[1]}"


def destination_not_source(lit: Literal) -> None:
    """A value read as 1.16.2 from prose that is in fact the 1.17 side of a recorded row."""
    global _MAP_1162
    if lit.build != "1.16.2" or lit.build_from.startswith("--build-of"):
        return
    if _MAP_1162 is None:
        _MAP_1162 = _load_1162_map()
    rva = lit.value - IMAGE_BASE if lit.form == "va" else lit.value
    if rva not in _MAP_1162 and rva in set(_MAP_1162.values()):
        lit.build = "1.17"
        lit.build_from += "; but it is the 1.17 side of a recorded 1.16.2 row"


def to_1171(img: Images, lit: Literal) -> tuple[int | None, str]:
    """The literal's rva in 1.17.1, or None and why."""
    global _MAP_1162
    rva = lit.value - IMAGE_BASE if lit.form == "va" else lit.value
    if lit.build == "1.17.1":
        return rva, ""
    if lit.build in ("1.17.0", "1.17"):
        try:
            moved, why = M1170.map_rva(rva)
        except M1170.Unmappable as exc:
            return None, str(exc)
        if lit.build == "1.17" and moved != rva:
            # "1.17" names either build. Ask the images which one this value belongs to.
            sec = img.section("1.17.1", rva)
            if sec == ".text":
                in_70, _ = img.is_boundary("1.17.0", rva)
                in_71, _ = img.is_boundary("1.17.1", rva)
                if in_71 and not in_70:
                    return rva, "tagged 1.17; a boundary in 1.17.1 only, so read as 1.17.1"
                if in_70 and not in_71:
                    return moved, "tagged 1.17; a boundary in 1.17.0 only, so read as 1.17.0"
                return None, "tagged 1.17, above the 1.17.0 -> 1.17.1 boundary, and the images do not say which"
        if moved != rva and img.section("1.17.0", rva) == ".text":
            ok, why = img.is_boundary("1.17.0", rva)
            if not ok:
                return None, f"not code in 1.17.0: {why}"
        return moved, why
    if lit.build == "1.16.2":
        if _MAP_1162 is None:
            _MAP_1162 = _load_1162_map()
        r170 = _MAP_1162.get(rva)
        if r170 is None:
            return None, "a 1.16.2 value, and no recorded 1.16.2 -> 1.17.0 row names it"
        try:
            moved, _ = M1170.map_rva(r170)
        except M1170.Unmappable as exc:
            return None, str(exc)
        return moved, f"1.16.2 -> 1.17.0 {r170:#x} by the recorded map"
    return None, f"build {lit.build} has no carry"


def stale_build(img: Images, lit: Literal, old: int, sec: str) -> str:
    """Why a value whose build was inferred rather than named looks like it belongs to another.

    A tag read off a comment block or the header can be wrong, and an untagged value defaults to
    1.17.1. Two cheap tests catch the costly mistakes: a 1.17.0 function entry in the region that
    moved `+0x70` is not an entry in 1.17.1, and a 1.16.2 function entry or global that the recorded
    map moved is not an entry or a read global in 1.17.1.
    """
    global _MAP_1162
    if lit.build_from.startswith(("comment line", "--build-of")):
        return ""
    rva = lit.value - IMAGE_BASE if lit.form == "va" else lit.value
    if sec == ".text":
        if img.is_entry("1.17.1", old):
            return ""
        if lit.build == "1.17.1" and rva >= BOUNDARY_1170 and img.is_entry("1.17.0", rva) \
                and img.is_entry("1.17.1", rva + 0x70):
            return f"a 1.17.0 function entry (1.17.1 has it at {IMAGE_BASE + rva + 0x70:#x}), not a 1.17.1 one"
    if lit.build not in ("1.17.1", "1.17.0", "1.17"):
        return ""
    if _MAP_1162 is None:
        _MAP_1162 = _load_1162_map()
    moved = _MAP_1162.get(rva)
    if moved is None or moved == rva:
        return ""
    if sec == ".text":
        return f"not a 1.17.1 function entry, and the recorded map moves 1.16.2 {IMAGE_BASE + rva:#x} " \
               f"to 1.17.0 {IMAGE_BASE + moved:#x}: a 1.16.2 value?"
    # A global some 1.17.1 code reads can still be the wrong one: 1.16.2's WorldChrMan slot holds
    # another global on 1.17. Agreement is the name, and the caller found none.
    row = _data_names_1162().get(rva)
    what = f" ({row[1]})" if row else ""
    return f"the recorded map moves 1.16.2 {IMAGE_BASE + rva:#x}{what} to 1.17.0 {IMAGE_BASE + moved:#x}, " \
           f"and nothing here says which build this one is"


def carry_literal(img: Images, lit: Literal) -> None:
    if lit.build == "1.17.2":
        lit.verdict, lit.note = "current", "already a 2.7.2.0 value"
        return
    old, why = to_1171(img, lit)
    if old is None:
        lit.verdict, lit.note = "unresolved", why
        return
    lit.rva_old = old
    sec = img.section("1.17.1", old)
    lit.kind = {".text": "code", ".data": "data", ".rdata": "rdata"}.get(sec, sec or "none")
    if sec not in (".text", ".data", ".rdata"):
        lit.verdict, lit.note = "unresolved", f"in {sec or 'no section'}, which does not carry"
        return
    if sec == ".text":
        ok, how = img.is_boundary("1.17.1", old)
        if not ok:
            lit.verdict, lit.note = "unverified", f"1.17.1 {IMAGE_BASE + old:#x} is {how}"
            return
    stale = stale_build(img, lit, old, sec)
    if stale:
        lit.verdict, lit.note = "unverified", stale
        return
    new = img.carry(old)
    if new is None:
        lit.verdict, lit.note = "unresolved", "the segment table refuses it (in or beside changed code)"
        return
    lit.new_rva = new
    if sec == ".text":
        ok, how = img.compare_code(old, new)
    else:
        ok, how = img.verify_data(old, new, sec)
    lit.verdict = "verified" if ok else "unverified"
    lit.note = (why + "; " if why else "") + how


# ----------------------------------------------------------------------------------------------
# rewriting


def _old_note(lit: Literal) -> str:
    va = lit.old_va
    rva = lit.value - IMAGE_BASE if lit.form == "va" else lit.value
    if lit.build == "1.17.1" or lit.rva_old == rva:
        # A 1.17.0 value that 1.17.1 did not move is the 1.17.1 value too.
        return f"1.17.1 {lit.text}"
    return f"{lit.build} {lit.text}, 1.17.1 {lit.rva_old + (IMAGE_BASE if lit.form == 'va' else 0):#x}" \
        if lit.rva_old is not None else f"{lit.build} {va:#x}"


@dataclass
class Signature:
    """Prologue bytes written beside an address, as a hex string or a byte array."""
    line: int
    col: int
    text: str
    values: list            # one int per byte, None for a `??` wildcard
    owner: Literal | None = None
    new_text: str = ""
    status: str = ""


SIG_STRING = re.compile(r"(['\"])((?:[0-9a-fA-F]{2}|\?\?)(?:\s*(?:[0-9a-fA-F]{2}|\?\?)){3,})\1")
SIG_ARRAY = re.compile(r"\[\s*((?:0x[0-9a-fA-F]{1,2}\s*,\s*){2,}0x[0-9a-fA-F]{1,2})\s*,?\s*\]")


def find_signatures(src: Source) -> list[Signature]:
    out = []
    for m in SIG_STRING.finditer(src.code):
        body = m.group(2)
        pairs = re.findall(r"[0-9a-fA-F]{2}|\?\?", body)
        line, col = src.line_col(m.start(2))
        out.append(Signature(line, col, body, [None if p == "??" else int(p, 16) for p in pairs]))
    for m in SIG_ARRAY.finditer(src.code):
        body = m.group(1)
        line, col = src.line_col(m.start(1))
        out.append(Signature(line, col, body, [int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{1,2})", body)]))
    return out


def carry_signatures(img: Images, sigs: list[Signature], lits: list[Literal]) -> None:
    """Pair each signature with the code literal whose 1.17.1 bytes it spells, and respell it."""
    old_data, new_data = img.image("1.17.1").data, img.image("1.17.2").data
    code = [x for x in lits if x.kind == "code" and x.rva_old is not None]
    for sig in sigs:
        n = len(sig.values)
        for lit in code:
            have = old_data[lit.rva_old:lit.rva_old + n]
            if all(v is None or v == b for v, b in zip(sig.values, have)):
                sig.owner = lit
                break
        if sig.owner is None:
            sig.status = "spells no carried literal's 1.17.1 bytes; left as written"
            continue
        lit = sig.owner
        if lit.verdict != "verified" or lit.new_rva is None:
            sig.status = f"spells {lit.text}, which is not verified; left as written"
            continue
        new = new_data[lit.new_rva:lit.new_rva + n]
        if all(v is None or v == b for v, b in zip(sig.values, new)):
            sig.status = f"spells {lit.text}; the same bytes on 2.7.2.0"
            continue
        it = iter(new)
        if sig.text.lstrip().startswith("0x"):
            def sub(m):
                b = next(it)
                digits = m.group(1)
                out = f"{b:0{len(digits)}x}"
                return "0x" + (out.upper() if digits.isupper() and any(c.isalpha() for c in digits) else out)
            sig.new_text = re.sub(r"0x([0-9a-fA-F]{1,2})", sub, sig.text)
        else:
            def sub(m):
                b = next(it)
                if m.group(0) == "??":
                    return "??"
                out = f"{b:02x}"
                return out.upper() if m.group(0).isupper() and any(c.isalpha() for c in m.group(0)) else out
            sig.new_text = re.sub(r"[0-9a-fA-F]{2}|\?\?", sub, sig.text)
        sig.status = f"spells {lit.text}; respelled for 2.7.2.0"


def rewrite(src: Source, lits: list[Literal], sigs: list[Signature] = ()) -> str:
    lines = list(src.lines)
    by_line: dict[int, list[Literal]] = {}
    for lit in lits:
        if lit.verdict == "verified" and lit.new_rva is not None and \
                lit.new_rva != (lit.value - IMAGE_BASE if lit.form == "va" else lit.value):
            by_line.setdefault(lit.line, []).append(lit)
    sig_lines: dict[int, list[Signature]] = {}
    for sig in sigs:
        if sig.new_text:
            sig_lines.setdefault(sig.line, []).append(sig)
    for line in sorted(set(by_line) | set(sig_lines)):
        group = by_line.get(line, [])
        text = lines[line - 1]
        body, nl = (text[:-1], "\n") if text.endswith("\n") else (text, "")
        edits = [(x.col, len(x.text), x.new_text()) for x in group] + \
            [(s.col, len(s.text), s.new_text) for s in sig_lines.get(line, [])]
        for col, n, new in sorted(edits, key=lambda e: -e[0]):
            body = body[:col] + new + body[col + n:]
        if group:
            note = "; ".join(_old_note(x) for x in sorted(group, key=lambda x: x.col))
            line_lo = src.line_at[line - 1]
            comment = src.comment_text(line)
            if comment and src.in_comment(line_lo + len(text.rstrip("\n")) - 1):
                body = body.rstrip() + f" ({note})"
            else:
                js = not src.py or src.in_multiline_string(line_lo + group[0].col)
                body = body.rstrip() + (f" // {note}" if js else f"  # {note}")
        lines[line - 1] = body + nl
    out = "".join(lines)
    if not src.py:
        out = _mark_agent(out, [x for x in lits if x.verdict in ("unverified", "unresolved")])
    return out


def _comment(text: str) -> list[str]:
    return [f"// {line}\n" for line in textwrap.wrap(text, 97)]


def _mark_agent(out: str, bad: list[Literal]) -> str:
    """Name this tool in the header, and refuse to load when anything was not verified.

    The note goes in as its own paragraph after the header's first one, so the paragraph that
    explains the first declaration stays beside it. The `throw` is the first statement.
    """
    lines = out.splitlines(keepends=True)
    top = 0
    while top < len(lines) and lines[top].lstrip().startswith("//"):
        top += 1
    note = _comment(f"Game addresses carried to 2.7.2.0 by scripts/{TOOL}.py; each moved literal "
                    f"keeps its old value in its comment.")
    gap = next((k for k in range(top) if lines[k].strip() == "//"), None)
    if gap is not None:
        lines[gap + 1:gap + 1] = note + ["//\n"]
        top += len(note) + 1
    else:
        lines[top:top] = (["//\n"] if top else []) + note
        top += len(note) + (1 if top else 0)
    if not bad:
        return "".join(lines)
    at = top
    while at < len(lines) and (lines[at].strip() == "" or lines[at].strip() in ("'use strict';", '"use strict";')):
        at += 1
    names = ", ".join(f"{x.old_va:#x} ({x.build}{', ' + x.name if x.name else ''})" for x in bad)
    block = _comment("Not verified on 2.7.2.0, so this agent refuses to load: "
                     + "; ".join(f"{x.old_va:#x} {x.note}" for x in bad))
    block.append(f"throw new Error('{TOOL}: not verified on 2.7.2.0: {names}');\n")
    lines[at:at] = block + ["\n"]
    return "".join(lines)


# ----------------------------------------------------------------------------------------------
# driver


@dataclass
class Report:
    path: Path
    literals: list[Literal] = field(default_factory=list)
    review: list = field(default_factory=list)
    signatures: list[Signature] = field(default_factory=list)
    refused: str = ""


def process(img: Images, path: Path, text: str, force: set[int], skip: set[int],
            builds: dict[int, str], skip_lines: set[int] = frozenset()) -> Report:
    rep = Report(path)
    if TOOL in text:
        rep.refused = f"already names {TOOL}; carried once already"
        return rep
    src = Source(path, text)
    lits, rep.review = find_literals(src, force, skip)
    lits = [x for x in lits if x.line not in skip_lines]
    rep.review = [r for r in rep.review if r[0] not in skip_lines]
    for lit in lits:
        if lit.value in builds:
            lit.build, lit.build_from = builds[lit.value], "--build-of"
        else:
            lit.build, lit.build_from = decide_build(src, lit)
            destination_not_source(lit)
            reinterpret_1162(lit)
        carry_literal(img, lit)
    rep.literals = lits
    rep.signatures = [s for s in find_signatures(src) if s.line not in skip_lines]
    carry_signatures(img, rep.signatures, lits)
    return rep


def print_report(rep: Report, root: Path) -> None:
    rel = rep.path.relative_to(root) if rep.path.is_relative_to(root) else rep.path
    if rep.refused:
        print(f"== {rel}: refused -- {rep.refused}")
        return
    n = len(rep.literals)
    v = sum(x.verdict == "verified" for x in rep.literals)
    c = sum(x.verdict == "verified" and x.new_rva is not None
            and x.new_rva != (x.value - IMAGE_BASE if x.form == "va" else x.value) for x in rep.literals)
    u = sum(x.verdict in ("unverified", "unresolved") for x in rep.literals)
    print(f"== {rel}: {n} game literals, {c} carried, {v} verified, {u} unresolved")
    for x in rep.literals:
        new = "-" if x.new_rva is None else (f"{x.new_rva + IMAGE_BASE:#x}" if x.form == "va" else f"{x.new_rva:#x}")
        print(f"  {x.line:4d}  {x.text:<13} {x.build:<6} {x.kind:<5} -> {new:<13} {x.verdict:<10} "
              f"[{x.build_from}; {x.basis}] {x.note}")
    for line, text, name in rep.review:
        print(f"  {line:4d}  {text:<13} review: in code, not taken for a game address{(' (' + name + ')') if name else ''}")
    for s in rep.signatures:
        print(f"  {s.line:4d}  bytes '{s.text[:24]}{'...' if len(s.text) > 24 else ''}' {s.status}"
              + (f" -> '{s.new_text[:24]}{'...' if len(s.new_text) > 24 else ''}'" if s.new_text else ""))


def parse_hexset(values: list[str]) -> set[int]:
    return {int(v, 16) for v in values}


def git_root(path: Path) -> Path:
    r = subprocess.run(["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, timeout=10, check=False)
    return Path(r.stdout.strip()) if r.returncode == 0 else REPO_ROOT


# ----------------------------------------------------------------------------------------------
# selftest


SELFTEST_JS = """\
// A synthetic agent. WorldChrMan below is 1.17.1.
//
// History, never rewritten: 1.16.2 0x143d65f88.
const game = Process.getModuleByName('eldenring.exe');
const ersc = Process.getModuleByName('ersc.dll');
const WORLD_CHR_MAN = ptr('0x143d69ff8');
const PRE_BEHAVIOR_RVA = 0x401f30;   // 1.17.1
const INVADE = 0x25850;
const STUB = 0x262890;
Interceptor.attach(game.base.add(PRE_BEHAVIOR_RVA), {});
Interceptor.attach(ersc.base.add(INVADE), {});
Interceptor.attach(game.base.add(STUB), {});
send({ note: 'read off the 1.17.0 dump 0x141f6d8d0 and carried' });
const HOOKS = [{ va: '0x1404e0a30', bytes16: '40574883ec60488bf9488b0d00db8903' }];
for (const rva of [0x401f30]) Interceptor.attach(game.base.add(rva), {});
"""

SELFTEST_PY = '''\
"""A synthetic tool; 1.17.1 throughout."""
WORLD_CHR_MAN_GLOBAL_RVA = 0x3D69FF8
AGENT = """
const w = ptr('0x143d69ff8');   // WorldChrMan
"""
'''


def selftest() -> int:
    failures = []

    def check(label, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}{(' -- ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    img = Images()

    print("addresses already proven on 2.7.2.0 (scripts/frida/arena-extents.js):")
    for text, form, want, kind in (("0x143d69ff8", "va", 0x143D6D018, "data"),
                                   ("0x140401f30", "va", 0x140401FC0, "code")):
        lit = Literal(1, 0, text, int(text, 16), form, "selftest")
        carry_literal(img, lit)
        got = None if lit.new_rva is None else lit.new_rva + IMAGE_BASE
        check(f"{text} -> {want:#x} {kind}", got == want and lit.verdict == "verified" and lit.kind == kind,
              f"got {got and hex(got)} {lit.verdict}: {lit.note}")

    print("refusals:")
    lit = Literal(1, 0, "0x140262890", 0x140262890, "va", "selftest")
    carry_literal(img, lit)
    check("0x140262890 (a different same-length stub in each build) is not carried",
          lit.verdict in ("unresolved", "unverified"), f"{lit.verdict}: {lit.note}")
    lit = Literal(1, 0, "0x140401f31", 0x140401F31, "va", "selftest")
    carry_literal(img, lit)
    check("0x140401f31 (inside an instruction) is not carried", lit.verdict == "unverified", lit.note)

    print("1.17.0 values chain through the +0x70 step:")
    entries, begins, _ = img.pdata("1.17.0")
    e71 = set(img.pdata("1.17.1")[1])
    pick = next(b for b in begins if b >= 0xB00000 and b + 0x70 in e71 and img.carry(b + 0x70) is not None
                and img.compare_code(b + 0x70, img.carry(b + 0x70))[0])
    lit = Literal(1, 0, f"{pick + IMAGE_BASE:#x}", pick + IMAGE_BASE, "va", "selftest", build="1.17.0")
    carry_literal(img, lit)
    check(f"1.17.0 {pick + IMAGE_BASE:#x} -> 1.17.1 {pick + 0x70 + IMAGE_BASE:#x} -> 2.7.2.0",
          lit.rva_old == pick + 0x70 and lit.new_rva == img.carry(pick + 0x70) and lit.verdict == "verified",
          f"{lit.verdict}: {lit.note}")

    print("a JavaScript agent:")
    src_path = Path("selftest.js")
    rep = process(img, src_path, SELFTEST_JS, set(), set(), {})
    vals = {x.text: x for x in rep.literals}
    check("comment literals are history and are not read", "0x143d65f88" not in vals)
    check("an ersc rva is not a game address", "0x25850" not in vals)
    check("the VA literal is carried", vals.get("0x143d69ff8") is not None and vals["0x143d69ff8"].verdict == "verified")
    check("an rva constant added to the game base is carried",
          vals.get("0x401f30") is not None and vals["0x401f30"].new_rva == 0x401FC0)
    check("the stub rva is found and not carried",
          vals.get("0x262890") is not None and vals["0x262890"].verdict in ("unresolved", "unverified"))
    check("an address quoted inside a sentence is history", "0x141f6d8d0" not in vals)
    check("an inline list a loop adds to the game base is read",
          sum(x.text == "0x401f30" for x in rep.literals) == 2)
    sig = next((s for s in rep.signatures if s.text.startswith("4057")), None)
    want = img.image("1.17.2").data[0x4e0b10:0x4e0b20].hex()
    check("a prologue string beside its address is respelled from the 2.7.2.0 image",
          sig is not None and sig.new_text == want and want != sig.text,
          f"{sig and sig.status} {sig and sig.new_text} want {want}")
    out = rewrite(Source(src_path, SELFTEST_JS), rep.literals, rep.signatures)
    check("--write writes the respelled prologue", f"bytes16: '{want}'" in out)
    check("--write rewrites the literal", "ptr('0x143d6d018')" in out and "0x401fc0;" in out, "")
    check("--write keeps the old value in the comment",
          "// 1.17.1 0x143d69ff8" in out and "// 1.17.1 (1.17.1 0x401f30)" in out)
    check("--write throws at load naming the unverified address",
          "throw new Error('frida-carry-1172: not verified on 2.7.2.0: 0x140262890" in out)
    check("--write leaves the ersc rva alone", "ersc.base.add(INVADE)" in out and "const INVADE = 0x25850;" in out)
    rep2 = process(img, src_path, out, set(), set(), {})
    check("a carried file is refused the second time", bool(rep2.refused))
    try:
        subprocess.run(["node", "--check", "-"], input=out, text=True, capture_output=True, timeout=20, check=True)
        check("the rewritten agent parses (node --check)", True)
    except FileNotFoundError:
        print("  skip  node is absent")
    except subprocess.CalledProcessError as exc:
        check("the rewritten agent parses (node --check)", False, exc.stderr.strip()[:200])

    print("a Python tool with an embedded agent:")
    rep = process(img, Path("selftest.py"), SELFTEST_PY, set(), set(), {})
    vals = {x.text: x for x in rep.literals}
    check("the docstring is not read", all(x.line != 1 for x in rep.literals))
    check("an _RVA constant is carried", vals.get("0x3D69FF8") is not None and vals["0x3D69FF8"].new_rva == 0x3D6D018)
    out = rewrite(Source(Path("selftest.py"), SELFTEST_PY), rep.literals)
    check("Python code gets a # comment, the embedded agent a // one",
          "= 0x3D6D018  # 1.17.1 0x3D69FF8" in out and "ptr('0x143d6d018');   // WorldChrMan (1.17.1 0x143d69ff8)" in out,
          "")
    try:
        compile(out, "selftest.py", "exec")
        check("the rewritten tool compiles", True)
    except SyntaxError as exc:
        check("the rewritten tool compiles", False, str(exc))

    print()
    if failures:
        print(f"selftest FAILED: {failures}")
        return 1
    print("selftest passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--write", action="store_true", help="rewrite each file (see --out-root)")
    ap.add_argument("--out-root", type=Path,
                    help="write to this tree at each file's path relative to its own checkout, "
                         "instead of in place")
    ap.add_argument("--rva", action="append", default=[], metavar="HEX",
                    help="take this in-code literal for a game rva even without a recognised use")
    ap.add_argument("--skip", action="append", default=[], metavar="HEX",
                    help="this literal is not a game address (a test fixture, a foreign module)")
    ap.add_argument("--skip-line", action="append", default=[], type=int, metavar="N",
                    help="leave every literal and signature on this line alone (a selftest fixture)")
    ap.add_argument("--build-of", action="append", default=[], metavar="HEX=BUILD",
                    help="the build a literal was written for, when the comments do not say")
    ap.add_argument("--json", type=Path, help="also write the reports as JSON")
    ap.add_argument("--explain", action="append", default=[], metavar="HEX",
                    help="carry one 1.17.1 address and print every reader or instruction compared")
    ap.add_argument("--witness", action="append", default=[], metavar="GLOBAL:OFF[:OFF...]",
                    help="confirm a field chain off a 1.17.1 global: twin instructions in both images "
                         "load the global and then read each offset in turn")
    ap.add_argument("--getter", action="append", default=[], metavar="OFF",
                    help="list leaf accessors `op [rcx+OFF]; ret` in 1.17.1 that are byte-identical "
                         "in 2.7.2.0")
    ap.add_argument("--function-offsets", action="append", default=[], metavar="VA",
                    help="compare a whole 1.17.1 function with its 2.7.2.0 twin and list the field "
                         "offsets it uses")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.function_offsets:
        img = Images()
        for raw in a.function_offsets:
            v = int(raw, 16)
            same, how, offs = img.function_offsets(v - IMAGE_BASE if v >= IMAGE_BASE else v)
            listed = " ".join(f"{k:#x}" + (f"(*{c})" if c > 1 else "") for k, c in sorted(offs.items()))
            print(f"{raw}: {'identical' if same else 'differs'} -- {how}\n    offsets: {listed}")
        return 0
    if a.witness or a.getter:
        img = Images()
        for spec in a.witness:
            parts = [int(x, 16) for x in spec.split(":")]
            g = parts[0] - IMAGE_BASE if parts[0] >= IMAGE_BASE else parts[0]
            n_old, n_carried, _, ex = img.witness_chain(g, parts[1:])
            chain = " -> ".join(f"+{o:#x}" for o in parts[1:])
            print(f"{IMAGE_BASE + g:#x} {chain}: {n_old} sites in 1.17.1, {n_carried} carried to the "
                  f"same chain in 2.7.2.0{(' (e.g. ' + ex + ')') if ex else ''}")
        for raw in a.getter:
            off = int(raw, 16)
            rows = img.getters(off)
            same = [r for r in rows if r[1] >= 0]
            print(f"+{off:#x}: {len(rows)} getters in 1.17.1, {len(same)} identical in 2.7.2.0")
            for s, n, text in rows[:6]:
                print(f"    {IMAGE_BASE + s:#x} -> {'-' if n < 0 else hex(IMAGE_BASE + n)}  {text}")
        return 0
    if a.explain:
        img = Images()
        for raw in a.explain:
            v = int(raw, 16)
            lit = Literal(0, 0, raw, v, "va" if v >= IMAGE_BASE else "rva", "--explain")
            carry_literal(img, lit)
            new = "-" if lit.new_rva is None else hex(lit.new_rva + IMAGE_BASE)
            print(f"{raw} {lit.kind} -> {new} {lit.verdict}: {lit.note}")
            if lit.kind in ("data", "rdata") and lit.new_rva is not None:
                img.verify_data(lit.rva_old, lit.new_rva, "." + lit.kind, explain=True)
            if lit.kind == "code" and lit.rva_old is not None:
                r = lit.rva_old
                entries, begins, ends = img.pdata("1.17.1")
                at = bisect.bisect_right(begins, r) - 1
                inside = entries[at] if at >= 0 and entries[at][0] <= r < entries[at][1] else None
                j = bisect.bisect_right(ends, r) - 1
                d = img.image("1.17.1").data
                print(f"    .pdata entry around it: {inside and tuple(hex(x) for x in inside)}; "
                      f"previous entry end {hex(ends[j]) if j >= 0 else '-'}")
                print(f"    1.17.1 bytes before {d[r - 16:r].hex()} at {d[r:r + 16].hex()}")
                # Decoded up to the end of the function it sits in: the bytes after that are
                # already printed raw above, and decoded they would only look like instructions.
                end = function_extent.body_slice_end(d, IMAGE_BASE + r, cap=24)
                if end is None:
                    print("      no function extent here, so nothing is decoded")
                else:
                    for i in img.md.disasm(d[r:end], r):
                        print(f"      {IMAGE_BASE + i.address:#x}  {i.mnemonic} {i.op_str}")
        return 0
    if not a.files:
        ap.print_help()
        return 2
    builds = {}
    for spec in a.build_of:
        k, _, b = spec.partition("=")
        builds[int(k, 16)] = b
    img = Images()
    reports = []
    rc = 0
    for path in a.files:
        path = path.resolve()
        text = path.read_text(encoding="utf-8")
        rep = process(img, path, text, parse_hexset(a.rva), parse_hexset(a.skip), builds, set(a.skip_line))
        root = git_root(path)
        print_report(rep, root)
        reports.append(rep)
        if any(x.verdict in ("unverified", "unresolved") for x in rep.literals):
            rc = 1
        if a.write and not rep.refused and rep.literals:
            out = rewrite(Source(path, text), rep.literals, rep.signatures)
            dest = (a.out_root.resolve() / path.relative_to(root)) if a.out_root else path
            # Another hand may already have carried the copy in the destination tree. Writing over
            # it would throw that work away, so only a copy still equal to the source is replaced.
            if a.out_root and dest.exists() and dest.read_text(encoding="utf-8") != text:
                print(f"  refused to write {dest}: it already differs from {path}")
                rc = 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(out, encoding="utf-8")
            print(f"  wrote {dest}")
    if a.json:
        a.json.write_text(json.dumps([
            {"path": str(r.path), "refused": r.refused, "review": r.review,
             "signatures": [{"line": s.line, "text": s.text, "new_text": s.new_text, "status": s.status}
                            for s in r.signatures],
             "literals": [{k: getattr(x, k) for k in ("line", "text", "form", "build", "build_from", "basis",
                                                         "kind", "rva_old", "new_rva", "verdict", "note")}
                          for x in r.literals]} for r in reports], indent=1), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
