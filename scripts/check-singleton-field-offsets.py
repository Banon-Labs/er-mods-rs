#!/usr/bin/env python3
"""Prove every repo struct-field offset that a singleton reaches is a field 2.7.2.0 reads.

Why this exists -- the one silent failure class
-----------------------------------------------
There are three ways for this workspace to be wrong about an address, and only two of them say
anything:

  * a wrong detour target        -> `er-hook` refuses the address and logs `HOOK REFUSED`;
  * a call or data rva on another build -> the resolver refuses it and the caller reports it;
  * a wrong struct field offset  -> `*(this + 0xNN)` returns the neighbouring field.

The third one has no refusal, no fault and no log line. It returns a plausible number of the
right width, forever. `crates/er-reload-trace/src/lib.rs` records the shape in miniature: a global
moved and the read against the old slot still "succeeded" -- it returned a byte from whatever now
lives there.

The mechanism (no dataflow, no RTTI pairing, no heuristics)
-----------------------------------------------------------
Find `mov r64, [rip+disp32]` whose target is a known singleton global, then take any
`[reg + 0xNN]` in the next few instructions, before that register is written again. The base
register provably holds that singleton -- the instruction two lines up loaded it. So the
displacement is an object-level ground truth: it is a real field offset of whatever class lives in
that global, in the 2.7.2.0 image (`eldenring-deobf-1.17.2.bin`, located by
`scripts/er_game_image.py`), with no inference in between. A repo constant whose owner is one of
those objects and whose value is in that set is cleared; one whose value is not is reported
`NOT-WITNESSED`, by name.

The singleton globals themselves are read out of the crates (`scripts/rva_symbols.py`): every
declaration of a chain's constant must agree on one value, and that value is the 2.7.2.0 rva the
scan looks for. A crate that drifts its copy of a singleton turns the chain into a problem here.

What it still does not cover, said plainly: most of the workspace's named offset constants have
no owner this can establish, or an owner no singleton reaches (CS::ChrIns, the dialog layouts,
Scaleform::MemoryFile, the FileCap family), and some belong to a scanned object but are never read
through the singleton (`PLAYER_GAME_DATA_IS_MAIN_PLAYER_OFFSET` 0x8f0, which the constructor and
vtable routes used for the pins in crates/er-game-base/src/pgd.rs do witness). All of those are
unknown. They are printed as unknown, counted as unknown, and never rolled into the clean number.

One chain hop
-------------
`CS::PlayerGameData` is not in a global of its own -- it hangs off `GameDataMan + 0x8`
(`main_player_game_data`). So a chain may carry hops: the scan follows
`mov r64,[rip+GameDataMan]; mov r64,[r64+0x8]` and then collects `[reg+0xNN]` off the result.
A hop is only admissible when the hop offset is itself witnessed on the parent object, and that
admission is re-derived on every run, never asserted.

What this gate reports, and why it reports coverage
----------------------------------------------------
A matcher that goes blind produces an empty set and `assert bad == 0` passes over an empty set.
So this prints three numbers, not a verdict:

  * how many repo constants it cleared (offset witnessed on its owner);
  * how many it could not attribute -- these are unknown, never "fine";
  * how many field offsets each scanned object contributed.

The per-object field counts depend only on the frozen image and this matcher, so they are frozen
exactly (`FROZEN_FIELD_COUNTS`): a change that blinds the matcher moves them and this gate goes red
instead of reporting a smaller clean set. The cleared count carries a floor
(`MIN_CLEARED_CONSTANTS`) and the not-witnessed count a ceiling (`MAX_NOT_WITNESSED`): a constant
edited onto a value its object never reads moves from the first bucket to the second, and the
ceiling is what turns that into a red gate rather than a longer list.

Usage
  python3 scripts/check-singleton-field-offsets.py                  # the gate
  python3 scripts/check-singleton-field-offsets.py --census         # per-object field census
  python3 scripts/check-singleton-field-offsets.py --pgd-offsets    # PGD witnesses (three routes),
                                                                    # the evidence behind the
                                                                    # offset_of! pins in
                                                                    # crates/er-game-base/src/pgd.rs
  python3 scripts/check-singleton-field-offsets.py --selftest
  python3 scripts/check-singleton-field-offsets.py --prove-selftest-catches-regression

capstone is auto-provisioned via `uv run --with capstone` when it is not importable. The image is
gitignored (game-derived), so a checkout without it skips -- loudly, and never with the word `OK`.
"""

import argparse
import collections
import importlib.util
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import er_game_image  # noqa: E402 - repo-local, and the sys.path line above is what makes it work
import function_extent  # noqa: E402 - repo-local, and the sys.path line above is what makes it work

# --- capstone bootstrap via uv (no persistent install needed) ------------------------------
if importlib.util.find_spec("capstone") is None:
    if os.environ.get("_ER_SFO_BOOTSTRAPPED") != "1":
        os.environ["_ER_SFO_BOOTSTRAPPED"] = "1"
        os.execvp(  # noqa: S606 -- fixed argv, no shell
            "uv",
            ["uv", "run", "--with", "capstone", "python3", os.path.abspath(__file__)]
            + sys.argv[1:],
        )
    sys.exit("capstone unavailable and `uv run --with capstone` bootstrap failed")

from capstone import CS_ARCH_X86, CS_GRP_JUMP, CS_GRP_RET, CS_MODE_64, Cs  # pyright: ignore[reportMissingImports]
from capstone import x86 as cs_x86  # pyright: ignore[reportMissingImports]

BASE = er_game_image.BASE
REPO = str(er_game_image.REPO)

# How many instructions after the singleton load may still be reading through it. The
# register-clobber, branch and function-end stops in `_follow` are what actually bound the window;
# this is the belt. The function-end stop is the one that matters: before it landed (2026-08-31) a
# wider window read phantom fields out of the next function, decoded from the de-Arxan'd image's
# leftover gap bytes. With it in place no window size changes the answer much, and 5 is what the
# frozen counts were measured at.
FOLLOW_INSNS = 5
# Widest plausible struct. Anything past this is an array stride or a mask, not a field.
FIELD_MAX = 0x20000

# ---------------------------------------------------------------------------------------------
# Which global holds which object.
#
# Every row is a claim, so each one is grounded by the repo's own named route. A wrong row here
# would compare a constant against another class's field set, so this table is deliberately short:
# an object nobody can ground is left out, and its constants are then reported `UNATTRIBUTED`
# rather than waved through.
#
#   object -> (singleton constant in the crates, hop offsets)
# ---------------------------------------------------------------------------------------------
CHAINS = {
    "CS::GameMan": ("GAME_MAN_SINGLETON_RVA", ()),
    "CS::GameDataMan": ("GAME_DATA_MAN_GLOBAL_RVA", ()),
    "CS::CSMenuManImp": ("CS_MENU_MAN_GLOBAL_RVA", ()),
    "CS::WorldChrManImp": ("WORLD_CHR_MAN_GLOBAL_RVA", ()),
    "SessionManager": ("CS_SESSION_MANAGER_GLOBAL_RVA", ()),
    "CS::CSFlipperImp": ("CS_FLIPPER_SINGLETON_RVA", ()),
    # One hop. `GameDataMan + 0x8` is `main_player_game_data` in the sibling binding, spelled
    # GAME_DATA_MAN_PLAYER_GAME_DATA_08_OFFSET (crates/er-game-base/src/rva.rs).
    "CS::PlayerGameData": ("GAME_DATA_MAN_GLOBAL_RVA", (0x8,)),
}

# Repo constants name their owner by prefix far more often than any curated table can keep up
# with. These are the prefixes whose meaning is unambiguous; anything else falls through to the
# curated owners table and then to `UNATTRIBUTED`.
NAME_PREFIX_OWNERS = (
    ("PLAYER_GAME_DATA_", "CS::PlayerGameData"),
    ("PGD_", "CS::PlayerGameData"),
    ("GAME_DATA_MAN_", "CS::GameDataMan"),
    ("GAME_MAN_", "CS::GameMan"),
    ("GAMEMAN_", "CS::GameMan"),
    ("WORLD_CHR_MAN_", "CS::WorldChrManImp"),
    ("CS_MENU_MAN_", "CS::CSMenuManImp"),
    ("CSMENUMAN_", "CS::CSMenuManImp"),
    ("MENU_MAN_", "CS::CSMenuManImp"),
    ("SESSION_MANAGER_", "SessionManager"),
    ("CS_FLIPPER_", "CS::CSFlipperImp"),
    ("FLIPPER_", "CS::CSFlipperImp"),
)

# A constant whose name says it is an offset. Deliberately narrow: a name that merely contains
# offset somewhere is noise, and inline `+ 0xNN` cannot be told from an index.
OFFSET_NAME = re.compile(r"(_OFFSET(_[A-Z0-9]+)?$|_OFF$|_OFFS$|_FIELD$|_DISP$|_DISPLACEMENT$)")

# ---------------------------------------------------------------------------------------------
# Frozen coverage, measured on eldenring-deobf-1.17.2.bin on 2026-10-23.
#
# The per-object counts are the field offsets witnessed through each singleton. They are a
# function of the frozen image and this matcher alone -- no repo source enters -- so they are
# pinned exactly. A change that blinds the scan (a dropped mov encoding, a narrowed follow window,
# a wrong global) moves them, and this gate goes red rather than reporting a smaller clean set.
#
# Refresh deliberately with --refresh-frozen when the matcher is intentionally widened, and read
# the diff.
# ---------------------------------------------------------------------------------------------
FROZEN_FIELD_COUNTS = {
    "CS::GameMan": 159,
    "CS::GameDataMan": 53,
    "CS::CSMenuManImp": 44,
    "CS::WorldChrManImp": 18,
    "SessionManager": 16,
    "CS::CSFlipperImp": 7,
    "CS::PlayerGameData": 15,
}

# Cleared and not-witnessed repo constants. These read repo source, which lands continuously, so
# the cleared count is a floor with headroom and the not-witnessed count a ceiling with none: a
# constant moved onto a value its object never reads is exactly one more not-witnessed row.
# Measured 2026-10-23: 77 cleared, 15 not witnessed (each named by the report).
MIN_CLEARED_CONSTANTS = 60
MAX_NOT_WITNESSED = 15


class Chain:
    """One resolved singleton route, with its field-offset set on the image."""

    def __init__(self, obj, const, hops):
        self.obj = obj
        self.const = const
        self.hops = hops
        self.rva = None
        self.fields = set()
        self.hop_ok = True
        self.hop_note = ""


def read_singletons(names):
    """`({constant: rva}, [problem])` for every chain constant, read out of the crates.

    Every declaration of a name in the workspace must resolve to the same single value; a crate
    that drifted its copy is a problem, not a vote.
    """
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import rva_symbols  # noqa: PLC0415 -- path is set up immediately above

    index = rva_symbols.index()
    out, problems = {}, []
    for name in sorted(set(names)):
        values = set()
        where = []
        for decl in index.decls:
            if decl.symbol != name:
                continue
            values |= {v for v in (decl.value or []) if isinstance(v, int)}
            where.append(os.path.relpath(decl.path, REPO))
        if len(values) != 1:
            problems.append(
                f"{name}: the crates declare it as {sorted(hex(v) for v in values) or 'nothing'} "
                f"({', '.join(sorted(set(where))) or 'no declaration'}); a chain needs one rva"
            )
            continue
        out[name] = values.pop()
    return out, problems


# `mov r64, [rip+disp32]`: REX.W (0x48) or REX.WR (0x4c), opcode 8B, modrm mod=00 rm=101.
# All 16 encodings, which is every way the compiler can load a qword global into a register.
RIP_MOV = re.compile(rb"[\x48\x4c]\x8b[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", re.S)

_MD = Cs(CS_ARCH_X86, CS_MODE_64)
_MD.detail = True


def _rip_mov_sites(image):
    """[(file_offset, dest_reg_index, target_rva)] for every `mov r64,[rip+d]` in `image`.

    Byte-matched first (a regex over 98 MB is milliseconds; decoding 98 MB is not), then each hit
    contributes only through `_follow`, which decodes it.
    """
    out = []
    for match in RIP_MOV.finditer(image):
        off = match.start()
        rex, _, modrm = image[off : off + 3]
        disp = int.from_bytes(image[off + 3 : off + 7], "little", signed=True)
        target = (off + 7) + disp  # file offset == rva, so this is already an rva
        if not 0 <= target < len(image):
            continue
        reg = ((modrm >> 3) & 7) | (0x8 if rex & 0x4 else 0)
        out.append((off, reg, target))
    return out


# capstone register ids for the 16 general-purpose 64-bit registers, indexed the way the REX+modrm
# encoding numbers them.
_R64 = [
    cs_x86.X86_REG_RAX, cs_x86.X86_REG_RCX, cs_x86.X86_REG_RDX, cs_x86.X86_REG_RBX,
    cs_x86.X86_REG_RSP, cs_x86.X86_REG_RBP, cs_x86.X86_REG_RSI, cs_x86.X86_REG_RDI,
    cs_x86.X86_REG_R8, cs_x86.X86_REG_R9, cs_x86.X86_REG_R10, cs_x86.X86_REG_R11,
    cs_x86.X86_REG_R12, cs_x86.X86_REG_R13, cs_x86.X86_REG_R14, cs_x86.X86_REG_R15,
]
_R64_INDEX = {reg: i for i, reg in enumerate(_R64)}


def _follow(image, off, reg_index, hops):
    """Field offsets read through the register loaded at `off`, after walking `hops`.

    Returns a set of displacements. The window closes at the first of: the end of the function the
    load is in, FOLLOW_INSNS instructions, a write to the register being tracked (after which it no
    longer holds the singleton), a branch, or a decode failure.

    The function end is the stop that was missing once, and it invented fields. The premise of
    this whole gate -- the base register holds that singleton because the instruction two lines up
    loaded it -- is a statement about straight-line code inside one function. Past the `ret` it is
    simply false: control does not fall through, the register belongs to whoever comes next, and
    in a de-Arxan'd image the bytes there are the deobfuscator's leftovers, which resynchronise
    into instructions nobody assembled (a `lea edx, [rax + 0x18]` six bytes into the next function
    was once this file's headline "new field"). The extent comes from the shared
    `function_extent` primitive, which merges `.pdata` chunk runs, so a read genuinely inside a
    split function is kept.
    """
    want = _R64[reg_index]
    found = set()
    remaining = list(hops)
    pos = off + 7
    seen = 0
    # None means the extent cannot be told at all. There is no honest byte count to substitute,
    # so the other stops (register clobber, branch, FOLLOW_INSNS) carry it.
    end = function_extent.body_end(image, BASE + off)
    for insn in _MD.disasm(bytes(image[pos : pos + 64]), BASE + pos):
        if seen >= FOLLOW_INSNS:
            break
        if end is not None and insn.address - BASE + insn.size > end:
            break
        seen += 1
        hop_taken = False
        for operand in insn.operands:
            if operand.type != cs_x86.X86_OP_MEM:
                continue
            mem = operand.mem
            if mem.base != want or mem.index != 0 or mem.segment != 0:
                continue
            if not 0 <= mem.disp <= FIELD_MAX:
                continue
            if remaining:
                # Still walking to the object: this displacement must be the hop, and the
                # instruction must be the load that takes us there.
                if mem.disp == remaining[0] and insn.id == cs_x86.X86_INS_MOV:
                    dest = insn.operands[0]
                    if dest.type == cs_x86.X86_OP_REG and dest.reg in _R64_INDEX:
                        want = dest.reg
                        remaining.pop(0)
                        seen = 0
                        hop_taken = True
                        break
            else:
                found.add(mem.disp)
        if hop_taken:
            continue
        # The register stops holding the singleton the moment anything writes it.
        _, written = insn.regs_access()
        if any(_MD.reg_name(r) == _MD.reg_name(want) for r in written):
            break
        if insn.group(CS_GRP_JUMP):
            break
    return found


def scan(image, targets, hops_by_target):
    """{target_rva: {offset, ...}} for every requested singleton rva in the image."""
    out = {rva: set() for rva in targets}
    for off, reg, target in _rip_mov_sites(image):
        if target not in out:
            continue
        out[target] |= _follow(image, off, reg, hops_by_target[target])
    return out


def build_chains(image, singletons, chains=CHAINS):
    """Resolve chains against the image. Returns ([Chain], [problem strings])."""
    problems = []
    resolved = []
    for obj, (const, hops) in chains.items():
        chain = Chain(obj, const, hops)
        if const not in singletons:
            problems.append(f"{obj}: {const} has no single rva in the crates")
            continue
        chain.rva = singletons[const]
        resolved.append(chain)

    # Pass 1: the hopless chains, which is also what the hop admission test reads.
    flat = [c for c in resolved if not c.hops]
    sets = scan(image, {c.rva for c in flat}, {c.rva: () for c in flat})
    for chain in flat:
        chain.fields = sets[chain.rva]

    # Pass 2: the chains with hops. A hop is admissible only when the hop offset is itself
    # witnessed on the parent object -- re-derived here, never asserted.
    hopped = [c for c in resolved if c.hops]
    for chain in hopped:
        parent = next((c for c in flat if c.const == chain.const), None)
        if parent is None:
            chain.hop_ok = False
            chain.hop_note = f"no hopless chain on {chain.const} to admit the hop against"
            problems.append(f"{chain.obj}: {chain.hop_note}")
            continue
        first = chain.hops[0]
        if first not in parent.fields:
            chain.hop_ok = False
            chain.hop_note = f"hop +{first:#x} is not witnessed on {parent.obj}"
            problems.append(f"{chain.obj}: {chain.hop_note}")
            continue
        chain.hop_note = f"via {parent.obj} +{first:#x} (witnessed)"
    good = [c for c in hopped if c.hop_ok]
    if good:
        sets = scan(image, {c.rva for c in good}, {c.rva: c.hops for c in good})
        for chain in good:
            chain.fields = sets[chain.rva]

    order = list(chains)
    resolved.sort(key=lambda c: order.index(c.obj))
    return resolved, problems


# ---------------------------------------------------------------------------------------------
# Repo side.
# ---------------------------------------------------------------------------------------------
def _curated_owners():
    """The hand-read constant -> class table from scripts/adjudicate-autoload-offsets.py.

    Imported rather than copied: a copied ownership table is a second claim about the same fact
    that drifts silently. A failure to import is a hard error, not a shrug -- a gate that quietly
    loses its ownership table reports fewer constants and still says `OK`.
    """
    path = os.path.join(REPO, "scripts", "adjudicate-autoload-offsets.py")
    spec = importlib.util.spec_from_file_location("_er_sfo_adjudicate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return {
        name: owner
        for name, owner in module.OWNERS.items()
        if isinstance(owner, str) and not owner.startswith("@")
    }


def owner_of(name, curated):
    short = name.split("::")[-1]
    if short in curated:
        return curated[short]
    for prefix, obj in NAME_PREFIX_OWNERS:
        if short.startswith(prefix):
            return obj
    return None


def repo_offsets():
    """[(constant, value, file, line)] for every named offset constant that resolves to a number."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import rva_symbols  # noqa: PLC0415 -- path is set up immediately above

    index = rva_symbols.index()
    rows = []
    for decl in index.decls:
        if not OFFSET_NAME.search(decl.symbol):
            continue
        values = decl.value or []
        for value in sorted(v for v in values if isinstance(v, int) and 0 <= v <= FIELD_MAX):
            rows.append((decl.qualified, value, os.path.relpath(decl.path, REPO), decl.line))
    return rows


def classify(rows, chains, curated):
    """Sort every repo offset row into cleared / not-witnessed / unattributed."""
    by_obj = {c.obj: c for c in chains}
    out = collections.defaultdict(list)
    for name, value, path, line in rows:
        obj = owner_of(name, curated)
        if obj is None:
            out["UNATTRIBUTED-NO-OWNER"].append((name, value, obj, path, line))
            continue
        chain = by_obj.get(obj)
        if chain is None or not chain.hop_ok:
            out["UNATTRIBUTED-NO-CHAIN"].append((name, value, obj, path, line))
            continue
        if value in chain.fields:
            out["CLEARED"].append((name, value, obj, path, line))
        else:
            out["NOT-WITNESSED"].append((name, value, obj, path, line))
    return out


# ---------------------------------------------------------------------------------------------
# PGD evidence mode (--pgd-offsets). Not part of the gate: nothing here feeds a verdict or a
# frozen count. It exists so the offsets pinned in crates/er-game-base/src/pgd.rs can be
# re-derived, instead of resting on a one-off script somebody ran once and deleted.
#
# Three routes, each proving the base register holds a PlayerGameData without any function
# pairing:
#
#   A  the singleton chain, GameDataMan(global) -> +0x8 -> PGD  (the gate's own route)
#   B  functions called with that chain's PGD pointer still in an argument register, one deep
#   C  the CS::PlayerGameData constructor, the functions it calls with `this` in RCX, and the
#      methods of the two vtables it stores at [this+0]
#
# The constructor is checked rather than trusted: it must store both declared vtable pointers at
# [this+0] (`CS::IGameDataElem` then `CS::PlayerGameData`, by RTTI), or this mode refuses to
# report. A drifted constructor address then says so instead of quietly scanning an unrelated
# function and printing a confident, wrong witness set.
# ---------------------------------------------------------------------------------------------
PGD_CTOR = 0x14025D550
PGD_VTABLES = (0x1429E65F8, 0x1429EAFA8)
ARG_REGS = (cs_x86.X86_REG_RCX, cs_x86.X86_REG_RDX, cs_x86.X86_REG_R8, cs_x86.X86_REG_R9)
VOLATILE = {
    cs_x86.X86_REG_RAX, cs_x86.X86_REG_RCX, cs_x86.X86_REG_RDX, cs_x86.X86_REG_R8,
    cs_x86.X86_REG_R9, cs_x86.X86_REG_R10, cs_x86.X86_REG_R11,
}
TEXT_LO, TEXT_HI = 0x140001000, 0x142900000
FUNC_SCAN_BYTES = 0x3000


def _walk_function(image, va, arg_reg=cs_x86.X86_REG_RCX):
    """Field offsets read through `arg_reg`, plus the vtable pointers stored at [reg+0].

    Bounded by the function's own extent, with FUNC_SCAN_BYTES surviving only as a cap on top of
    it. The `ret` stop below is not a substitute: a function that ends in a tail call never
    reaches one.
    """
    held = {arg_reg}
    fields, calls, stored = set(), [], []
    lea_targets = {}
    off = va - BASE
    stop = function_extent.body_slice_end(image, va, cap=FUNC_SCAN_BYTES)
    if stop is None:
        stop = off + FUNC_SCAN_BYTES
    for insn in _MD.disasm(bytes(image[off:stop]), va):
        if (
            insn.id == cs_x86.X86_INS_MOV
            and len(insn.operands) == 2
            and insn.operands[0].type == cs_x86.X86_OP_REG
            and insn.operands[1].type == cs_x86.X86_OP_REG
            and insn.operands[1].reg in held
            and insn.operands[0].reg in _R64_INDEX
        ):
            held.add(insn.operands[0].reg)
            continue
        if (
            insn.id == cs_x86.X86_INS_LEA
            and insn.operands[1].type == cs_x86.X86_OP_MEM
            and insn.operands[1].mem.base == cs_x86.X86_REG_RIP
        ):
            lea_targets[insn.operands[0].reg] = insn.address + insn.size + insn.operands[1].mem.disp
        for operand in insn.operands:
            if operand.type != cs_x86.X86_OP_MEM:
                continue
            mem = operand.mem
            if mem.base in held and mem.index == 0 and 0 <= mem.disp <= FIELD_MAX:
                fields.add(mem.disp)
                if (
                    mem.disp == 0
                    and insn.id == cs_x86.X86_INS_MOV
                    and insn.operands[1].type == cs_x86.X86_OP_REG
                    and insn.operands[1].reg in lea_targets
                ):
                    stored.append(lea_targets[insn.operands[1].reg])
        if insn.id == cs_x86.X86_INS_CALL:
            if (
                cs_x86.X86_REG_RCX in held
                and insn.operands
                and insn.operands[0].type == cs_x86.X86_OP_IMM
            ):
                calls.append(insn.operands[0].imm)
            held -= VOLATILE
            continue
        if insn.operands and insn.operands[0].type == cs_x86.X86_OP_REG and insn.operands[0].reg in held:
            same = (
                insn.id == cs_x86.X86_INS_MOV
                and len(insn.operands) == 2
                and insn.operands[1].type == cs_x86.X86_OP_REG
                and insn.operands[1].reg in held
            )
            if not same:
                held.discard(insn.operands[0].reg)
        if insn.group(CS_GRP_RET):
            break
    return fields, calls, stored


def _chain_call_targets(image, global_rva, hop, follow=12):
    """(fields, {(callee, arg_reg)}) for the PGD the chain produces, over the whole image."""
    fields, targets = set(), set()
    for off, reg_index, target in _rip_mov_sites(image):
        if target != global_rva:
            continue
        want, hopped, seen = _R64[reg_index], False, 0
        # Same function-end stop as `_follow`, for the same reason: a chain hop read out of the
        # next function is not this singleton's field.
        end = function_extent.body_end(image, BASE + off)
        for insn in _MD.disasm(bytes(image[off + 7 : off + 7 + 128]), BASE + off + 7):
            if seen >= follow:
                break
            if end is not None and insn.address - BASE + insn.size > end:
                break
            seen += 1
            took = False
            for operand in insn.operands:
                if operand.type != cs_x86.X86_OP_MEM:
                    continue
                mem = operand.mem
                if mem.base != want or mem.index != 0:
                    continue
                if not hopped:
                    if (
                        mem.disp == hop
                        and insn.id == cs_x86.X86_INS_MOV
                        and insn.operands[0].type == cs_x86.X86_OP_REG
                    ):
                        want, hopped, seen, took = insn.operands[0].reg, True, 0, True
                        break
                elif 0 <= mem.disp <= FIELD_MAX:
                    fields.add(mem.disp)
            if took:
                continue
            if (
                hopped
                and insn.id == cs_x86.X86_INS_CALL
                and want in ARG_REGS
                and insn.operands
                and insn.operands[0].type == cs_x86.X86_OP_IMM
            ):
                targets.add((insn.operands[0].imm, want))
                break
            _, written = insn.regs_access()
            if any(_MD.reg_name(r) == _MD.reg_name(want) for r in written):
                break
    return fields, targets


def _vtable_methods(image, vtable_va, limit=64):
    off = vtable_va - BASE
    out = []
    for i in range(limit):
        word = int.from_bytes(image[off + i * 8 : off + i * 8 + 8], "little")
        if not TEXT_LO <= word < TEXT_HI:
            break
        out.append(word)
    return out


def _pgd_fields(image, global_rva):
    fields, targets = _chain_call_targets(image, global_rva, 0x8)
    routes = {"A-chain-direct": len(fields), "B-callees": len(targets)}
    for callee, arg_reg in sorted(targets):
        if TEXT_LO <= callee < TEXT_HI:
            more, _, _ = _walk_function(image, callee, arg_reg=arg_reg)
            fields |= more
    ctor_fields, ctor_calls, stored = _walk_function(image, PGD_CTOR)
    declared = set(PGD_VTABLES)
    if not declared.issubset(set(stored)):
        return None, routes, (
            f"the constructor at {PGD_CTOR:#x} does not store "
            + " and ".join(f"{v:#x}" for v in sorted(declared))
            + f" at [this+0] (it stores {sorted(hex(s) for s in set(stored))}); the address or the "
            "vtables drifted, so route C is refusing to report rather than scan the wrong function"
        )
    fields |= ctor_fields
    for callee in ctor_calls:
        if TEXT_LO <= callee < TEXT_HI:
            more, _, _ = _walk_function(image, callee)
            fields |= more
    for vtable in PGD_VTABLES:
        for method in _vtable_methods(image, vtable):
            more, _, _ = _walk_function(image, method)
            fields |= more
    routes["C-ctor+vtable"] = len(ctor_calls)
    return fields, routes, None


def pgd_evidence(image, chains):
    """Print every CS::PlayerGameData field offset the image reads, by route."""
    chain = next(c for c in chains if c.obj == "CS::PlayerGameData")
    fields, routes, err = _pgd_fields(image, chain.rva)
    if err:
        print(f"FAIL: {err}")
        return 1
    print(f"routes {routes}")
    print(f"2.7.2.0 reads {len(fields)} PGD offsets")
    for offset in sorted(fields):
        gate = "  (gate route A)" if offset in chain.fields else ""
        print(f"{offset:#06x}{gate}")
    return 0


def report(chains, buckets, problems, out=sys.stdout):
    print("objects scanned (field offsets witnessed through the singleton):", file=out)
    for chain in chains:
        note = f"  [{chain.hop_note}]" if chain.hop_note else ""
        if not chain.hop_ok:
            print(f"  {chain.obj:24s} UNUSABLE{note}", file=out)
            continue
        print(f"  {chain.obj:24s} {chain.rva:#x}  {len(chain.fields):4d} field offsets{note}", file=out)
    cleared = buckets["CLEARED"]
    print(file=out)
    print("repo offset constants:", file=out)
    print(f"  CLEARED               {len(cleared):5d}  (witnessed on its owner)", file=out)
    print(
        f"  NOT-WITNESSED         {len(buckets['NOT-WITNESSED']):5d}  "
        "(owner is scanned, offset never read through the singleton -- unknown, not safe)",
        file=out,
    )
    print(
        f"  UNATTRIBUTED-NO-CHAIN {len(buckets['UNATTRIBUTED-NO-CHAIN']):5d}  "
        "(owner known, no singleton reaches it -- unknown)",
        file=out,
    )
    print(
        f"  UNATTRIBUTED-NO-OWNER {len(buckets['UNATTRIBUTED-NO-OWNER']):5d}  "
        "(no owner could be established -- unknown)",
        file=out,
    )
    per_obj = collections.Counter(row[2] for row in cleared)
    if per_obj:
        print("  cleared by owner: " + ", ".join(f"{k} {v}" for k, v in sorted(per_obj.items())), file=out)
    # Name the not-witnessed rows. They are the ones a reader can act on: the owner is scanned, so
    # the offset simply never appears through this route, and calling that "clean" is the exact
    # move this gate refuses to make on anyone's behalf.
    for name, value, obj, path, line in sorted(buckets["NOT-WITNESSED"]):
        print(f"    NOT-WITNESSED  {name} = {value:#x}  on {obj}  ({path}:{line})", file=out)
    for problem in problems:
        print(f"  PROBLEM: {problem}", file=out)


def check_frozen(chains, buckets, out=sys.stdout):
    """The coverage assertions. A drop must be as visible as a mismatch."""
    failures = []
    seen = {c.obj: c for c in chains}
    for obj, frozen in sorted(FROZEN_FIELD_COUNTS.items()):
        chain = seen.get(obj)
        if chain is None or not chain.hop_ok:
            failures.append(f"{obj}: frozen at {frozen} but the chain did not resolve")
            continue
        if len(chain.fields) != frozen:
            failures.append(
                f"{obj}: {len(chain.fields)} field offsets != frozen {frozen} -- the matcher "
                "changed what it can see; re-read the diff before refreshing"
            )
    extra = sorted(set(seen) - set(FROZEN_FIELD_COUNTS))
    if extra:
        failures.append(f"objects scanned but not frozen: {', '.join(extra)}")
    cleared = len(buckets["CLEARED"])
    if cleared < MIN_CLEARED_CONSTANTS:
        failures.append(
            f"cleared {cleared} constants, floor is {MIN_CLEARED_CONSTANTS} -- coverage dropped; "
            "this is a finding, not a smaller clean run"
        )
    unwitnessed = buckets["NOT-WITNESSED"]
    if len(unwitnessed) > MAX_NOT_WITNESSED:
        failures.append(
            f"{len(unwitnessed)} constants name a field their object never reads, ceiling is "
            f"{MAX_NOT_WITNESSED}: a constant was moved onto a value the image does not read "
            "through its singleton (the NOT-WITNESSED list above names it)"
        )
    for line in failures:
        print(f"FAIL: {line}", file=out)
    return 1 if failures else 0


def selftest(image, singletons, out=None):
    """Live check, then a planted wrong constant and an unwitnessed hop -- so green is not vacuous."""
    chains, problems = build_chains(image, singletons)
    curated = _curated_owners()
    buckets = classify(repo_offsets(), chains, curated)
    out = sys.stdout if out is None else out
    # The full coverage report belongs to the live run, which check.sh prints. Here it is captured
    # and replayed only when something is wrong, so the selftest does not double every number.
    captured = io.StringIO()
    report(chains, buckets, problems, out=captured)
    rc = check_frozen(chains, buckets, out=captured)
    print(
        f"selftest: {sum(len(c.fields) for c in chains if c.hop_ok)} field offsets witnessed "
        f"across {sum(1 for c in chains if c.hop_ok)} objects, "
        f"{len(buckets['CLEARED'])} repo constants cleared",
        file=out,
    )
    if rc:
        print(captured.getvalue(), file=out, end="")
        print("selftest FAIL: the live check is red, so its controls prove nothing", file=out)
        return 1
    if problems:
        print(f"selftest FAIL: {len(problems)} unresolved chain problem(s)", file=out)
        return 1

    # Negative control 1 -- a constant on a value its object never reads must be caught. For each
    # object, plant a constant one byte above the largest witnessed offset (which no read reaches),
    # and require it to land in the not-witnessed bucket and, once that bucket is past its
    # ceiling, to turn the gate red.
    caught = 0
    planted = 0
    for chain in chains:
        if not chain.hop_ok or not chain.fields:
            continue
        victim = max(chain.fields) + 1
        label = f"{chain.obj.split('::')[-1]}_PLANTED_OFFSET"
        rows = [(label, victim, "synthetic", 0)]
        fake = classify(rows, chains, {label: chain.obj})
        planted += 1
        if len(fake["NOT-WITNESSED"]) == 1:
            merged = collections.defaultdict(list, {k: list(v) for k, v in buckets.items()})
            merged["NOT-WITNESSED"] += fake["NOT-WITNESSED"] * (MAX_NOT_WITNESSED + 1)
            if check_frozen(chains, merged, out=io.StringIO()):
                caught += 1
    if planted == 0:
        print("selftest FAIL: could not plant a control -- nothing was witnessed", file=out)
        return 1
    if caught != planted:
        print(f"selftest FAIL: planted {planted} unread offsets, caught {caught}", file=out)
        return 1
    print(f"  negative control: {caught}/{planted} planted unread offsets rejected", file=out)

    # Negative control 2 -- the hop admission must actually gate. A hop offset that is not
    # witnessed on the parent has to make the chain unusable, not silently scan garbage.
    bogus = dict(CHAINS)
    bogus["CS::PlayerGameData"] = ("GAME_DATA_MAN_GLOBAL_RVA", (0x7,))
    _, hop_problems = build_chains(image, singletons, chains=bogus)
    if not any("hop +0x7" in p for p in hop_problems):
        print("selftest FAIL: an unwitnessed hop offset was admitted", file=out)
        return 1
    print("  hop control: an unwitnessed hop (+0x7) is refused", file=out)
    print("selftest OK", file=out)
    return 0


def prove_selftest_catches_regression(image, singletons):
    """Blind the matcher on purpose; --selftest must go red.

    This narrows the follow window to zero instructions -- the single most likely way for this
    scan to quietly stop seeing fields -- and requires the whole selftest to fail.
    """
    global FOLLOW_INSNS
    real = FOLLOW_INSNS
    FOLLOW_INSNS = 0
    seen = io.StringIO()
    try:
        code = selftest(image, singletons, out=seen)
    finally:
        FOLLOW_INSNS = real
    lines = [line for line in seen.getvalue().splitlines() if line.startswith(("FAIL", "selftest FAIL"))]
    if code == 0:
        print("FAIL: the selftest passed with a blinded matcher -- it is vacuous")
        return 1
    print("regression proof OK: FOLLOW_INSNS=0 (a matcher that sees no fields) fails the selftest.")
    print("  what the blinded run reported:")
    for line in lines[:6]:
        print(f"    {line}")
    if len(lines) > 6:
        print(f"    ... and {len(lines) - 6} more")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--census", action="store_true", help="print every witnessed offset per object")
    parser.add_argument(
        "--pgd-offsets",
        action="store_true",
        help="print the CS::PlayerGameData witnesses (the evidence behind the offset_of! pins)",
    )
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument(
        "--prove-selftest-catches-regression",
        action="store_true",
        help="blind the matcher on purpose; the selftest must go red",
    )
    parser.add_argument(
        "--refresh-frozen", action="store_true", help="print FROZEN_FIELD_COUNTS for this matcher"
    )
    args = parser.parse_args()

    # The de-Arxan'd image is gitignored (game-derived bytes are never committed), so a fresh
    # checkout and CI do not have it. Skip at exit 0 -- but say skipped, name the missing file,
    # and never print the word this gate prints when it passes.
    path = er_game_image.image_path()
    if path is None:
        print(f"SKIPPED (NOT A PASS): {er_game_image.missing_reason()}, so no field offset was checked at all")
        return 0

    singletons, problems = read_singletons(name for name, _hops in CHAINS.values())
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    image = path.read_bytes()

    if args.prove_selftest_catches_regression:
        return prove_selftest_catches_regression(image, singletons)
    if args.selftest:
        return selftest(image, singletons)

    chains, problems = build_chains(image, singletons)

    if args.refresh_frozen:
        print("FROZEN_FIELD_COUNTS = {")
        for chain in chains:
            print(f'    "{chain.obj}": {len(chain.fields)},')
        print("}")
        return 0

    if args.census:
        for chain in chains:
            print(f"== {chain.obj}  ({chain.const} {chain.rva:#x})")
            print("   ", " ".join(f"{o:#x}" for o in sorted(chain.fields)))
        return 0

    if args.pgd_offsets:
        return pgd_evidence(image, chains)

    curated = _curated_owners()
    buckets = classify(repo_offsets(), chains, curated)
    report(chains, buckets, problems)
    return check_frozen(chains, buckets)


if __name__ == "__main__":
    sys.exit(main())
