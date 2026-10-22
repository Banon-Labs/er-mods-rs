#!/usr/bin/env python3
"""Compile er-cel-shade's HLSL into the DXIL blobs the DLL embeds, or check they are current.

The DLL embeds `crates/er-cel-shade/shaders/compiled/*.dxil` with `include_bytes!`, so building
it needs no shader compiler. Those blobs are compiled here from the HLSL beside them, with dxc's
own validator signing each container -- a native D3D12 runtime refuses unsigned DXIL, while
vkd3d-proton accepts either, so an unsigned blob would pass every Linux run and fail on Windows.

No game bytes are committed: the root signature the original passes embed is not attached,
because every port variant is created with the game pipeline's own root signature.

Every blob is checked before it is written, the same checks the DLL runs on a runtime-compiled
variant (design 7.6), so a port that would be refused in game is refused here:

- the container is well formed, its declared size is its length, it carries no root signature,
  and its program is a shader model 6.0 pixel shader;
- the digest is signed: it is DXC's retail hash of the bytes after it, recomputed here the way
  `crates/er-cel-shade/src/dxbc.rs` computes it;
- when the game's original is in the vkd3d shader dump, the port links like it -- `ISG1` and
  `OSG1` equal element for element apart from the read and write mask, and `PSV0` interpolation
  modes equal -- binds nothing but the original's resources and the editor's space42 parameters,
  and needs no feature (`SFI0`) the original does not. Without the dump this part is skipped and
  says so.

Each port is compiled with `ER_VARIANT_ID` set to its port kind shifted into the top byte, the
kind being its index in `REPLACEMENTS` in `crates/er-cel-shade/src/replace.rs`; the probe header a
port writes carries it.

Usage:
    python3 scripts/build-cel-shaders.py            # compile, check, and write the blobs
    python3 scripts/build-cel-shaders.py --check    # recompile to a temp dir, check, compare
    python3 scripts/build-cel-shaders.py --selftest

dxc is found at `$DXC`, else `~/tools/dxc/bin/dxc` (the layout `er-shaderlab setup` installs). With
no dxc, `--check` still checks the committed blobs and skips only the staleness comparison, the
same line `check-game-version-supported.py` draws for an absent game.

The dump is `$ER_VKD3D_DUMP_ROOT` when set, else `.worktrees/er-shaders/target/vkd3d-dump/run1/spv`
under the nearest directory from this checkout upward that has it, which finds the main tree's
dump from inside a worktree. It is a test input only and nothing from it is written anywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHADERS = REPO / "crates" / "er-cel-shade" / "shaders"
COMPILED = SHADERS / "compiled"
REPLACE_RS = REPO / "crates" / "er-cel-shade" / "src" / "replace.rs"
DUMP_RELATIVE = Path(".worktrees/er-shaders/target/vkd3d-dump/run1/spv")
TIMEOUT_SECONDS = 20

# The validator version stamped into each container's `dx.valver`. The game's own passes carry 1.6
# (`dxc -dumpbin` on the dumped originals) and it ships no Agility SDK, so on Windows the system's
# D3D12 runtime is the one that reads these blobs. Matching the game keeps ours inside what that
# runtime already accepts from the shaders they replace; dxc 1.9 would otherwise stamp 1.9.
VALIDATOR_VERSION = "1.6"

# HLSL 2021: short-circuiting `&&`, `||` and `?:`, and the language the runtime compiler (design
# 7.5) passes, so an embedded port and its runtime-compiled twin parse the same source alike.
HLSL_VERSION = "2021"

# The look is not compiled in: every shader reads it from the editor's parameter block
# (`shaders/editor_abi.hlsli`), which the in-game menu fills. The only defines left pick which of
# the game's two variants of a pass a source compiles to.
#
# output name -> (source, defines). The port kind, and with it `ER_VARIANT_ID`, comes from the
# blob's place in `REPLACEMENTS`.
TARGETS = {
    "light_deferred_ao.dxil": ("light_deferred_ao.hlsl", []),
    "light_deferred_ao_ssr.dxil": ("light_deferred_ao.hlsl", ["CEL_SSR"]),
    "light_cmb_albedo.dxil": ("light_cmb_albedo.hlsl", []),
    "tonemap.dxil": ("tonemap.hlsl", []),
    "tonemap_lut.dxil": ("tonemap.hlsl", ["CEL_LUT"]),
}

# Where the port kind sits in `ER_VARIANT_ID`; the bits below are a runtime build number.
VARIANT_KIND_SHIFT = 24

# The space42 parameters a port may bind beyond its original's resources (design 5.1), as
# (`PSVResourceType`, space, lower bound, upper bound): A, the parameter block at b0; B, the probe
# at u1; C, the mask at u0; D, the duplicate-draw constants at b1.
PSV_RESOURCE_CBV = 2
PSV_RESOURCE_UAV_RAW = 7
EDITOR_SPACE = 42
EDITOR_BINDS = {
    (PSV_RESOURCE_CBV, EDITOR_SPACE, 0, 0),
    (PSV_RESOURCE_UAV_RAW, EDITOR_SPACE, 1, 1),
    (PSV_RESOURCE_UAV_RAW, EDITOR_SPACE, 0, 0),
    (PSV_RESOURCE_CBV, EDITOR_SPACE, 1, 1),
}


class Refused(Exception):
    """A blob that must not be embedded, and why."""


# ---------------------------------------------------------------------------------------------
# The DXBC container, read the way `crates/er-cel-shade/src/dxbc.rs` reads it. Every read is
# bounds-checked and a bad container raises `Refused` with what was wrong.
# ---------------------------------------------------------------------------------------------

HEADER_BYTES = 32
DIGEST = slice(4, 20)
HASHED_FROM = 20


def u32(data: bytes, at: int, what: str) -> int:
    if at < 0 or at + 4 > len(data):
        raise Refused(f"{what} is truncated")
    return struct.unpack_from("<I", data, at)[0]


@dataclass
class Container:
    data: bytes
    parts: list[tuple[bytes, bytes]]

    def part(self, code: bytes) -> bytes | None:
        return next((data for found, data in self.parts if found == code), None)

    def required(self, code: bytes) -> bytes:
        found = self.part(code)
        if found is None:
            raise Refused(f"no {code.decode()} part")
        return found


def parse_container(blob: bytes) -> Container:
    """The header and the part table, with every part checked to lie inside the declared size."""
    if blob[:4] != b"DXBC":
        raise Refused("not a DXBC container")
    total = u32(blob, 24, "the container header")
    if total > len(blob):
        raise Refused(f"the header declares {total} bytes and the blob has {len(blob)}")
    data = blob[:total]
    count = u32(data, 28, "the container header")
    if count == 0:
        raise Refused("the container has no parts")
    table_end = HEADER_BYTES + 4 * count
    if table_end > total:
        raise Refused("the part table is truncated")
    parts = []
    for index in range(count):
        offset = u32(data, HEADER_BYTES + 4 * index, "the part table")
        size = u32(data, offset + 4, f"part {index}")
        if offset < table_end or offset + 8 + size > total:
            raise Refused(f"part {index} lies outside the container")
        parts.append((data[offset:offset + 4], data[offset + 8:offset + 8 + size]))
    return Container(data, parts)


# MD5's per-step additive constants and rotations.
MD5_K = [int(abs(math.sin(step + 1)) * 2**32) & 0xFFFFFFFF for step in range(64)]
MD5_S = [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4
MD5_INIT = (0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476)


def md5_block(state: tuple[int, int, int, int], block: bytes) -> tuple[int, int, int, int]:
    """MD5's compression function: one 64-byte block into the state."""
    m = struct.unpack("<16I", block)
    a, b, c, d = state
    for step in range(64):
        if step < 16:
            f, g = (b & c) | (~b & d), step
        elif step < 32:
            f, g = (d & b) | (~d & c), (5 * step + 1) % 16
        elif step < 48:
            f, g = b ^ c ^ d, (3 * step + 5) % 16
        else:
            f, g = c ^ (b | ~d), (7 * step) % 16
        mixed = (f + a + MD5_K[step] + m[g]) & 0xFFFFFFFF
        mixed = ((mixed << MD5_S[step]) | (mixed >> (32 - MD5_S[step]))) & 0xFFFFFFFF
        a, d, c, b = d, c, b, (b + mixed) & 0xFFFFFFFF
    return tuple((x + y) & 0xFFFFFFFF for x, y in zip(state, (a, b, c, d)))


def retail_hash(container: bytes) -> bytes:
    """DXC's `ComputeHashRetail` over bytes `[20:]`: the digest a validator writes at `[4:20]`.

    MD5's compression function with DXC's own padding and no MD5 finalisation. The last block
    carries the length twice: the bit count as its first word, and `1 | bytes << 1` as its last.
    When the data's last partial row holds 56 bytes or more, the `0x80` terminator and zeros close
    that row and a row of its own carries the two words. The length is DXC's 32-bit `UINT`.
    """
    hashed = container[HASHED_FROM:]
    length = len(hashed) & 0xFFFFFFFF
    whole = len(hashed) - len(hashed) % 64
    state = MD5_INIT
    for at in range(0, whole, 64):
        state = md5_block(state, hashed[at:at + 64])
    rest = hashed[whole:]
    bits = struct.pack("<I", (length << 3) & 0xFFFFFFFF)
    if len(rest) < 56:
        tail = bits + rest + b"\x80"
    else:
        state = md5_block(state, (rest + b"\x80").ljust(64, b"\0"))
        tail = bits
    tail = tail.ljust(60, b"\0") + struct.pack("<I", (1 | (length << 1)) & 0xFFFFFFFF)
    state = md5_block(state, tail)
    return struct.pack("<4I", *state)


def is_signed_correctly(container: Container) -> bool:
    digest = container.data[DIGEST]
    return digest != bytes(16) and digest == retail_hash(container.data)


@dataclass(frozen=True)
class SigElement:
    stream: int
    name: str
    semantic_index: int
    system_value: int
    component_type: int
    register: int
    mask: int
    rw_mask: int
    min_precision: int

    def links_like(self, other: SigElement) -> bool:
        """Same slot, same way: every field but the read and write mask, which records what this
        shader's code does with the element rather than where it is."""
        return (self.stream, self.name, self.semantic_index, self.system_value,
                self.component_type, self.register, self.mask, self.min_precision) == (
            other.stream, other.name, other.semantic_index, other.system_value,
            other.component_type, other.register, other.mask, other.min_precision)


def parse_signature(part: bytes, what: str) -> list[SigElement]:
    """`DxilProgramSignatureElement`s: 32 bytes each, names as offsets into the part."""
    count = u32(part, 0, what)
    first = u32(part, 4, what)
    if first + 32 * count > len(part):
        raise Refused(f"{what}: the element table is truncated")
    elements = []
    for index in range(count):
        at = first + 32 * index
        name_at = u32(part, at + 4, what)
        end = part.find(b"\0", name_at)
        if name_at >= len(part) or end < 0:
            raise Refused(f"{what}: element {index} has no terminated name")
        try:
            name = part[name_at:end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise Refused(f"{what}: element {index}'s name is not UTF-8") from error
        elements.append(SigElement(
            stream=u32(part, at, what),
            name=name,
            semantic_index=u32(part, at + 8, what),
            system_value=u32(part, at + 12, what),
            component_type=u32(part, at + 16, what),
            register=u32(part, at + 20, what),
            mask=part[at + 24],
            rw_mask=part[at + 25],
            min_precision=u32(part, at + 28, what),
        ))
    return elements


@dataclass
class Psv0:
    # (type, space, lower, upper, kind, flags); kind and flags are None in 16-byte records.
    binds: list[tuple]
    # Interpolation modes of the input, output and patch elements, or None for runtime info
    # version 0, which has no signature elements.
    interpolation: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]] | None


def parse_psv0(part: bytes) -> Psv0:
    """The runtime info, the resource binds, and from version 1 the string table, the semantic
    index table and the signature elements, whose byte 13 is the interpolation mode."""
    what = "PSV0"
    at = 0

    def take(length: int) -> bytes:
        nonlocal at
        if length < 0 or at + length > len(part):
            raise Refused(f"{what} is truncated")
        chunk = part[at:at + length]
        at += length
        return chunk

    def word() -> int:
        return struct.unpack("<I", take(4))[0]

    runtime = take(word())
    if len(runtime) < 24:
        raise Refused("PSV0 runtime info is smaller than version 0")
    binds = []
    resources = word()
    if resources:
        record = word()
        if record < 16:
            raise Refused("PSV0 bind records are smaller than version 0")
        for _ in range(resources):
            fields = take(record)
            kind_flags = struct.unpack_from("<2I", fields, 16) if record >= 24 else (None, None)
            binds.append(struct.unpack_from("<4I", fields, 0) + kind_flags)
    if len(runtime) < 36:
        return Psv0(binds, None)
    inputs, outputs, patch = runtime[28], runtime[29], runtime[30]
    take(word())  # the string table
    take(4 * word())  # the semantic index table
    modes: list[int] = []
    if inputs + outputs + patch:
        record = word()
        if record < 16:
            raise Refused("PSV0 signature elements are smaller than version 0")
        modes = [take(record)[13] for _ in range(inputs + outputs + patch)]
    return Psv0(binds, (tuple(modes[:inputs]), tuple(modes[inputs:inputs + outputs]),
                        tuple(modes[inputs + outputs:])))


def sfi0(container: Container) -> int:
    """The feature flags. DXC writes `SFI0` for every program, a zero one included, so an absent
    part is refused rather than read as no features."""
    part = container.required(b"SFI0")
    if len(part) != 8:
        raise Refused("SFI0 is not 8 bytes")
    return struct.unpack("<Q", part)[0]


def program_model(container: Container) -> tuple[int, int, int]:
    """(shader kind, major, minor) from the `DXIL` part's program header; kind 0 is pixel."""
    part = container.required(b"DXIL")
    if len(part) < 24 or part[8:12] != b"DXIL":
        raise Refused("the DXIL part has no program header")
    version = u32(part, 0, "the DXIL program header")
    return version >> 16, (version >> 4) & 0xF, version & 0xF


def fnv1_64(data: bytes) -> int:
    """FNV-1 64, multiply before xor: vkd3d-proton's shader hash, which names its dump files."""
    value = 0xCBF29CE484222325
    for byte in data:
        value = ((value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF) ^ byte
    return value


# ---------------------------------------------------------------------------------------------
# The checks.
# ---------------------------------------------------------------------------------------------

def check_port(blob: bytes) -> Container:
    """What a blob must be on its own: well formed, signed, no root signature, `ps_6_0`."""
    container = parse_container(blob)
    if len(container.data) != len(blob):
        raise Refused(f"{len(blob) - len(container.data)} bytes follow the declared container")
    if container.data[DIGEST] == bytes(16):
        raise Refused("unsigned: the digest is zero; is libdxil.so beside dxc?")
    if not is_signed_correctly(container):
        raise Refused("the digest is not the retail hash of the container")
    if container.part(b"RTS0") is not None:
        raise Refused("carries a root signature part, which this build must not embed")
    if program_model(container) != (0, 6, 0):
        raise Refused(f"not a ps_6_0 program: kind, major, minor = {program_model(container)}")
    return container


def check_against_original(port: Container, original: Container) -> None:
    """Linkage, resources and features against the game's own pixel shader (design 7.6)."""
    for code, label in ((b"ISG1", "input"), (b"OSG1", "output")):
        ours = parse_signature(port.required(code), f"the port's {code.decode()}")
        theirs = parse_signature(original.required(code), f"the original's {code.decode()}")
        if len(ours) != len(theirs) or not all(a.links_like(b) for a, b in zip(ours, theirs)):
            raise Refused(f"the {label} signature differs from the original's: "
                          f"{[(e.name, e.semantic_index, e.register) for e in ours]} vs "
                          f"{[(e.name, e.semantic_index, e.register) for e in theirs]}")
    port_psv = parse_psv0(port.required(b"PSV0"))
    original_psv = parse_psv0(original.required(b"PSV0"))
    if port_psv.interpolation is None or port_psv.interpolation != original_psv.interpolation:
        raise Refused(f"interpolation modes differ: {port_psv.interpolation} vs "
                      f"{original_psv.interpolation}")
    allowed = set(original_psv.binds)
    extra = [bind for bind in port_psv.binds
             if bind not in allowed and tuple(bind[:4]) not in EDITOR_BINDS]
    if extra:
        raise Refused(f"binds resources the original does not, outside the space42 parameters: "
                      f"{extra}")
    ours, theirs = sfi0(port), sfi0(original)
    if ours & ~theirs:
        raise Refused(f"needs features the original does not: SFI0 0x{ours:x} vs 0x{theirs:x}")


@dataclass(frozen=True)
class Port:
    blob_name: str
    kind: int
    original_len: int
    original_fnv1: int


def ports_from_replace_rs(text: str) -> list[Port]:
    """Each `Replacement { ... }` that embeds a blob, in `REPLACEMENTS` order, which is the port
    kind."""
    ports = []
    for body in re.findall(r"Replacement\s*\{(.*?)\}", text, re.S):
        blob = re.search(r'include_bytes!\("\.\./shaders/compiled/([^"]+)"\)', body)
        length = re.search(r"original_len:\s*([0-9_]+)", body)
        fnv = re.search(r"original_fnv1:\s*0x([0-9a-fA-F_]+)", body)
        if blob and length and fnv:
            ports.append(Port(blob.group(1), len(ports), int(length.group(1).replace("_", "")),
                              int(fnv.group(1).replace("_", ""), 16)))
    return ports


def load_ports() -> dict[str, Port]:
    ports = {port.blob_name: port for port in ports_from_replace_rs(REPLACE_RS.read_text())}
    if set(ports) != set(TARGETS):
        raise SystemExit(f"build-cel-shaders: REPLACEMENTS embeds {sorted(ports)} and TARGETS "
                         f"builds {sorted(TARGETS)}; the two must name the same blobs")
    return ports


def dump_root() -> Path | None:
    configured = os.environ.get("ER_VKD3D_DUMP_ROOT", "").strip()
    if configured:
        return Path(configured) if Path(configured).is_dir() else None
    for directory in (REPO, *REPO.parents):
        if (directory / DUMP_RELATIVE).is_dir():
            return directory / DUMP_RELATIVE
    return None


def original_for(port: Port, root: Path | None) -> Container | None:
    """The game's pixel shader this port replaces, from the dump, or None when it is absent. A
    file whose length or FNV-1 is not the pin is refused rather than compared."""
    if root is None:
        return None
    path = root / f"{port.original_fnv1:016x}.dxil"
    if not path.is_file():
        return None
    data = path.read_bytes()
    if len(data) != port.original_len or fnv1_64(data) != port.original_fnv1:
        raise Refused(f"{path} is not the original REPLACEMENTS pins "
                      f"({len(data)} bytes, FNV-1 {fnv1_64(data):016x})")
    return parse_container(data)


def check_all(blobs: dict[str, bytes], ports: dict[str, Port]) -> list[str]:
    """Check every blob, raising `SystemExit` on the first refusal; returns one line per blob."""
    root = dump_root()
    lines = []
    for name, blob in blobs.items():
        try:
            container = check_port(blob)
            original = original_for(ports[name], root)
            if original is not None:
                check_against_original(container, original)
        except Refused as refusal:
            raise SystemExit(f"build-cel-shaders: {name} refused: {refusal}") from refusal
        compared = "links like its original" if original is not None else "original not compared"
        lines.append(f"{name}: kind {ports[name].kind}, {len(blob)} bytes, signed, {compared}")
    if root is None:
        lines.append("no vkd3d shader dump (set ER_VKD3D_DUMP_ROOT); originals not compared")
    return lines


# ---------------------------------------------------------------------------------------------
# Compiling.
# ---------------------------------------------------------------------------------------------

def find_dxc() -> Path | None:
    candidates = [os.environ.get("DXC"), str(Path.home() / "tools" / "dxc" / "bin" / "dxc")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    return None


def dxc_argv(dxc: Path, source: Path, defines: list[str], kind: int, out: Path) -> list[str]:
    argv = [str(dxc), "-T", "ps_6_0", "-E", "ps_main", "-O3", "-Qstrip_debug",
            "-HV", HLSL_VERSION, "-validator-version", VALIDATOR_VERSION,
            "-D", f"ER_VARIANT_ID={kind << VARIANT_KIND_SHIFT}"]
    for define in defines:
        argv += ["-D", define]
    return argv + [str(source), "-Fo", str(out)]


def compile_all(dxc: Path, out_dir: Path, ports: dict[str, Port]) -> dict[str, bytes]:
    blobs = {}
    for name, (source, defines) in TARGETS.items():
        out = out_dir / name
        argv = dxc_argv(dxc, SHADERS / source, defines, ports[name].kind, out)
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if proc.returncode != 0:
            raise SystemExit(f"dxc failed for {name}:\n{proc.stderr}")
        blobs[name] = out.read_bytes()
    return blobs


# ---------------------------------------------------------------------------------------------
# The selftest: synthetic containers, built and signed here, so it needs neither dxc nor the dump.
# ---------------------------------------------------------------------------------------------

def build_container(parts: list[tuple[bytes, bytes]]) -> bytes:
    """A container of `parts`, signed with the retail hash."""
    table = HEADER_BYTES + 4 * len(parts)
    offsets, body = [], b""
    for code, data in parts:
        offsets.append(table + len(body))
        body += code + struct.pack("<I", len(data)) + data
    total = table + len(body)
    unsigned = (b"DXBC" + bytes(16) + struct.pack("<HHII", 1, 0, total, len(parts))
                + struct.pack(f"<{len(parts)}I", *offsets) + body)
    return sign(unsigned)


def sign(blob: bytes) -> bytes:
    return blob[:4] + retail_hash(blob) + blob[20:]


def signature_part(elements: list[tuple]) -> bytes:
    """(name, register, system value, mask[, read and write mask]) per element, float32; the read
    and write mask defaults to the mask."""
    strings_at = 8 + 32 * len(elements)
    strings, records = b"", b""
    for name, register, system_value, mask, *rw in elements:
        records += struct.pack("<6IBBHI", 0, strings_at + len(strings), 0, system_value, 3,
                               register, mask, rw[0] if rw else mask, 0, 0)
        strings += name.encode() + b"\0"
    return struct.pack("<2I", len(elements), 8) + records + strings


def psv0_part(binds: list[tuple[int, int, int, int, int, int]], inputs: list[int],
              outputs: list[int]) -> bytes:
    runtime = bytearray(36)
    runtime[28], runtime[29] = len(inputs), len(outputs)
    data = struct.pack("<I", len(runtime)) + bytes(runtime) + struct.pack("<I", len(binds))
    if binds:
        data += struct.pack("<I", 24) + b"".join(struct.pack("<6I", *bind) for bind in binds)
    data += struct.pack("<2I", 0, 0)  # no strings, no semantic indexes
    elements = inputs + outputs
    if elements:
        data += struct.pack("<I", 16)
        data += b"".join(bytes(13) + bytes([mode]) + bytes(2) for mode in elements)
    return data


PROGRAM_PS_6_0 = struct.pack("<2I4s3I", 0x60, 6, b"DXIL", 0x106, 16, 0)
NO_PERSPECTIVE, LINEAR = 4, 2
TEXTURE_T0 = (3, 0, 0, 0, 2, 0)
SAMPLER_S1 = (1, 0, 1, 1, 14, 0)
EDITOR_CBV = (PSV_RESOURCE_CBV, EDITOR_SPACE, 0, 0, 13, 0)
EDITOR_PROBE = (PSV_RESOURCE_UAV_RAW, EDITOR_SPACE, 1, 1, 11, 0)


def synthetic(outputs: list[tuple] | None = None, binds: list[tuple] | None = None,
              features: int = 0, modes: list[int] | None = None,
              extra: list[tuple[bytes, bytes]] | None = None, program: bytes = PROGRAM_PS_6_0,
              inputs: list[tuple] | None = None) -> bytes:
    """A signed pixel shader container: SV_Position and TEXCOORD in, SV_Target0 out by default."""
    inputs = inputs if inputs is not None else [("SV_Position", 0, 1, 0xF), ("TEXCOORD", 1, 0, 0x3)]
    outputs = outputs if outputs is not None else [("SV_Target", 0, 64, 0xF)]
    binds = binds if binds is not None else [TEXTURE_T0, SAMPLER_S1]
    modes = modes if modes is not None else [NO_PERSPECTIVE, LINEAR]
    return build_container([
        (b"SFI0", struct.pack("<Q", features)),
        (b"ISG1", signature_part(inputs)),
        (b"OSG1", signature_part(outputs)),
        (b"PSV0", psv0_part(binds, modes, [0] * len(outputs))),
        (b"DXIL", program),
    ] + (extra or []))


def refusal(blob: bytes, original: bytes | None = None) -> str | None:
    """Why `blob` is refused, or None when it passes."""
    try:
        container = check_port(blob)
        if original is not None:
            check_against_original(container, parse_container(original))
    except Refused as error:
        return str(error)
    return None


def selftest() -> int:
    failures = []

    def expect(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    def refused(name: str, blob: bytes, original: bytes | None, says: str) -> None:
        why = refusal(blob, original)
        if why is None or says not in why:
            failures.append(f"{name}: want a refusal naming {says!r}, got {why!r}")

    # The compression function is MD5's: standard MD5 built on it matches hashlib at every length
    # around the block boundaries.
    for length in range(0, 200):
        message = bytes((7 * i + length) & 0xFF for i in range(length))
        padded = message + b"\x80" + bytes((55 - length) % 64) + struct.pack("<Q", 8 * length)
        state = MD5_INIT
        for at in range(0, len(padded), 64):
            state = md5_block(state, padded[at:at + 64])
        if struct.pack("<4I", *state) != hashlib.md5(message).digest():
            failures.append(f"md5 compression differs from hashlib at length {length}")
            break

    # DXC's padding: the committed blobs carry DXC's own digests.
    remainders = set()
    for name in TARGETS:
        path = COMPILED / name
        if path.is_file():
            blob = path.read_bytes()
            expect(f"retail hash of {name}", retail_hash(blob), blob[DIGEST])
            remainders.add("one-row" if (len(blob) - HASHED_FROM) % 64 < 56 else "two-row")
    print(f"selftest: committed blobs pin the retail hash's padding: "
          f"{', '.join(sorted(remainders)) or 'none committed'}")

    # A synthetic port against itself passes, and every rule refuses its own defect.
    good = synthetic()
    expect("a port that links like its original", refusal(good, good), None)
    expect("space42 parameters beyond the original's resources",
           refusal(synthetic(binds=[TEXTURE_T0, SAMPLER_S1, EDITOR_CBV, EDITOR_PROBE]), good),
           None)
    flipped = bytearray(good)
    flipped[7] ^= 1
    refused("a flipped digest byte", bytes(flipped), good, "not the retail hash")
    changed = bytearray(good)
    changed[-1] ^= 1
    refused("a changed byte after the digest", bytes(changed), good, "not the retail hash")
    refused("a zero digest", good[:4] + bytes(16) + good[20:], good, "unsigned")
    refused("an extra SV_Target",
            synthetic(outputs=[("SV_Target", 0, 64, 0xF), ("SV_Target", 1, 64, 0xF)]),
            good, "output signature")
    refused("a moved output", synthetic(), synthetic(outputs=[("SV_Target", 1, 64, 0xF)]),
            "output signature")
    refused("a narrower input", synthetic(inputs=[("SV_Position", 0, 1, 0xF), ("TEXCOORD", 1, 0, 0x1)]),
            good, "input signature")
    expect("an input the port also reads",
           refusal(synthetic(inputs=[("SV_Position", 0, 1, 0xF, 0x3), ("TEXCOORD", 1, 0, 0x3)]),
                   synthetic(inputs=[("SV_Position", 0, 1, 0xF, 0x0), ("TEXCOORD", 1, 0, 0x3)])),
           None)
    refused("another interpolation mode", synthetic(modes=[NO_PERSPECTIVE, NO_PERSPECTIVE]),
            good, "interpolation")
    refused("a texture the original does not bind",
            synthetic(binds=[TEXTURE_T0, SAMPLER_S1, (3, 0, 5, 5, 2, 0)]), good,
            "binds resources")
    refused("a space42 register the ABI does not have",
            synthetic(binds=[TEXTURE_T0, (PSV_RESOURCE_UAV_RAW, EDITOR_SPACE, 2, 2, 11, 0)]),
            good, "binds resources")
    refused("a feature the original does not need", synthetic(features=0x4000), good,
            "needs features")
    expect("a feature the original also needs",
           refusal(synthetic(features=0x10), synthetic(features=0x11)), None)
    refused("a root signature part", synthetic(extra=[(b"RTS0", bytes(8))]), None,
            "root signature")
    refused("a vertex shader", synthetic(program=struct.pack("<2I4s3I", 0x10060, 6, b"DXIL",
                                                             0x106, 16, 0)),
            None, "ps_6_0")
    refused("bytes past the declared size", good + b"\0", None, "follow the declared")
    refused("a truncated blob", good[:-1], None, "declares")
    refused("other magic", b"DXBD" + good[4:], None, "not a DXBC")
    for cut in range(0, len(good), 7):
        if refusal(good[:cut]) is None:
            failures.append(f"a blob cut at {cut} bytes passed")
            break

    # The `REPLACEMENTS` reader takes the order and the pins, and ignores the struct definition.
    sample = """
        pub struct Replacement { pub name: &'static str, pub dxil: &'static [u8] }
        Replacement { name: "A", original_len: 9_264, original_fnv1: 0xf6f6_f959_abff_547f,
                      dxil: include_bytes!("../shaders/compiled/a.dxil") },
        Replacement { name: "B", original_len: 10, original_fnv1: 0x1,
                      kind: PortKind::B, dxil: include_bytes!("../shaders/compiled/b.dxil") },
    """
    expect("REPLACEMENTS order and pins", ports_from_replace_rs(sample), [
        Port("a.dxil", 0, 9264, 0xF6F6F959ABFF547F), Port("b.dxil", 1, 10, 0x1)])
    expect("the committed REPLACEMENTS name every target",
           sorted(port.blob_name for port in ports_from_replace_rs(REPLACE_RS.read_text())),
           sorted(TARGETS))
    expect("FNV-1 64 of nothing", fnv1_64(b""), 0xCBF29CE484222325)
    expect("FNV-1 64 of a", fnv1_64(b"a"), 0xAF63BD4C8601B7BE)
    expect("the variant id argument", dxc_argv(Path("dxc"), Path("s.hlsl"), ["CEL_LUT"], 4,
                                               Path("o"))[11:16],
           ["-D", "ER_VARIANT_ID=67108864", "-D", "CEL_LUT", "s.hlsl"])

    for name, (source, _) in TARGETS.items():
        if not (SHADERS / source).is_file():
            failures.append(f"{name}: source {source} is missing")
    for failure in failures:
        print(f"selftest FAIL: {failure}", file=sys.stderr)
    print("selftest:", "FAIL" if failures else "ok")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed blobs are stale "
                        "or would be refused")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    ports = load_ports()
    dxc = find_dxc()
    if args.check:
        committed = {name: (COMPILED / name).read_bytes() for name in TARGETS
                     if (COMPILED / name).is_file()}
        missing = sorted(set(TARGETS) - set(committed))
        if missing:
            print(f"build-cel-shaders: missing -> {', '.join(missing)}", file=sys.stderr)
            return 1
        for line in check_all(committed, ports):
            print(f"build-cel-shaders: {line}")
        if dxc is None:
            print("build-cel-shaders: no dxc ($DXC or ~/tools/dxc/bin/dxc); skipping the staleness "
                  "check")
            return 0
        with tempfile.TemporaryDirectory() as tmp:
            fresh = compile_all(dxc, Path(tmp), ports)
        stale = [name for name, blob in fresh.items() if committed[name] != blob]
        if stale:
            print(f"build-cel-shaders: stale -> {', '.join(stale)}; run "
                  f"python3 {REPO}/scripts/build-cel-shaders.py", file=sys.stderr)
            return 1
        print(f"build-cel-shaders: {len(fresh)} blob(s) current")
        return 0
    if dxc is None:
        print("build-cel-shaders: no dxc found; set $DXC", file=sys.stderr)
        return 1
    # Compiled and checked aside, so a refused blob never replaces a committed one.
    with tempfile.TemporaryDirectory() as tmp:
        blobs = compile_all(dxc, Path(tmp), ports)
    lines = check_all(blobs, ports)
    COMPILED.mkdir(parents=True, exist_ok=True)
    for name, blob in blobs.items():
        (COMPILED / name).write_bytes(blob)
    for line in lines:
        print(f"build-cel-shaders: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
