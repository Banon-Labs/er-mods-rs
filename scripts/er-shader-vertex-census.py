#!/usr/bin/env python3
"""What a vertex-stage shader design has to know about the game's shaders, read from a vkd3d dump.

The dump (`VKD3D_SHADER_DUMP_PATH`) names each container by the FNV-1 64 of its bytes, the same
function `fnv1_64` in `crates/er-cel-shade` computes, so a hash printed here is the key the DLL
sees when the game creates a pipeline. The pipeline cache vkd3d-proton writes during the same
session (`*.cache.write`) holds one entry per pipeline, and each entry names the container hash of
every stage it uses. That cache is the only offline record of which shaders share a pipeline.

Sections, each printed with the counts it measured:

* `stages`: every container by its DXIL program kind, cross-checked against the stage byte PSV0
  carries.
* `rootsig`: the dumped root signature blobs and every distinct `RTS0` part, flags decoded, with
  each parameter's visibility.
* `vs`: vertex-shader output shapes, outputs named `POSITION` beside `SV_Position`,
  system-value inputs, and PSV0 binds.
* `pipelines`: stage sets in the cache, and the named passes that use HS, DS or GS.
* `passes`: the pass vocabulary from debug names, per stage, and which pixel shaders each
  vertex shader is paired with.
* `reads`: the cbuffer members and resources each vertex, hull, domain and geometry shader reads,
  from `dxc -dumpbin`, what each vertex-shader output is computed from, and a per-pass profile.
  `loadInput` and `storeOutput` name a signature element by its DXIL element ID, which is the
  order PSV0 lists elements in and not the register order ISG1 and OSG1 list them in; every ID is
  mapped through PSV0 (`by_element_id`).

Usage:

    python3 scripts/er-shader-vertex-census.py [--dump <dir>] [--cache <file>] [--dxc <path>]
        [--no-disasm] [--json <out>] [--section <name> ...]
    python3 scripts/er-shader-vertex-census.py --selftest

The dump root defaults to `ER_VKD3D_DUMP_ROOT`, then to
`.worktrees/er-shaders/target/vkd3d-dump/run1/spv` under the main checkout. The cache defaults to
the `cache/*.cache.write` beside that directory.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DUMP_RELATIVE = Path(".worktrees/er-shaders/target/vkd3d-dump/run1/spv")
DXC_DEFAULT = Path.home() / "tools" / "dxc" / "bin" / "dxc"
# A bound on one `dxc -dumpbin` call, which takes milliseconds; it only stops a wedged child.
DUMPBIN_SECONDS = 20

KIND_NAMES = {0: "ps", 1: "vs", 2: "gs", 3: "hs", 4: "ds", 5: "cs", 6: "lib"}
# `VkShaderStageFlagBits`, the stage field of a vkd3d-proton pipeline-blob chunk.
VK_STAGE = {0x1: "vs", 0x2: "hs", 0x4: "ds", 0x8: "gs", 0x10: "ps", 0x20: "cs"}
# `D3D_NAME`.
SYSTEM_VALUES = {
    0: "", 1: "POS", 2: "CLIPDST", 3: "CULLDST", 4: "RTINDEX", 5: "VPINDEX", 6: "VERTID",
    7: "PRIMID", 8: "INSTID", 9: "FFACE", 10: "SAMPLE", 11: "QUADEDGE", 12: "QUADINT",
    13: "TRIEDGE", 14: "TRIINT", 15: "LINEDET", 16: "LINEDEN", 64: "TARGET", 65: "DEPTH",
    66: "COVERAGE", 67: "DEPTHGE", 68: "DEPTHLE",
}
COMPONENT_TYPES = {0: "?", 1: "u32", 2: "i32", 3: "f32", 4: "u16", 5: "i16", 6: "f16"}
# `D3D12_ROOT_SIGNATURE_FLAGS`.
RS_FLAGS = [
    (0x1, "ALLOW_INPUT_ASSEMBLER_INPUT_LAYOUT"), (0x2, "DENY_VERTEX_SHADER_ROOT_ACCESS"),
    (0x4, "DENY_HULL_SHADER_ROOT_ACCESS"), (0x8, "DENY_DOMAIN_SHADER_ROOT_ACCESS"),
    (0x10, "DENY_GEOMETRY_SHADER_ROOT_ACCESS"), (0x20, "DENY_PIXEL_SHADER_ROOT_ACCESS"),
    (0x40, "ALLOW_STREAM_OUTPUT"), (0x80, "LOCAL_ROOT_SIGNATURE"),
    (0x100, "DENY_AMPLIFICATION_SHADER_ROOT_ACCESS"), (0x200, "DENY_MESH_SHADER_ROOT_ACCESS"),
    (0x400, "CBV_SRV_UAV_HEAP_DIRECTLY_INDEXED"), (0x800, "SAMPLER_HEAP_DIRECTLY_INDEXED"),
]
VISIBILITY = {0: "all", 1: "vs", 2: "hs", 3: "ds", 4: "gs", 5: "ps", 6: "as", 7: "ms"}
PARAM_TYPES = {0: "table", 1: "constants", 2: "cbv", 3: "srv", 4: "uav"}
RANGE_TYPES = {0: "t", 1: "u", 2: "b", 3: "s"}
PSV_RESOURCE = {0: "invalid", 1: "sampler", 2: "cbv", 3: "srv_typed", 4: "srv_raw",
                5: "srv_structured", 6: "uav_typed", 7: "uav_raw", 8: "uav_structured",
                9: "uav_structured_counter"}
# Pass tokens in the material shaders' debug names (`M[...]_Gbuf@[cl].vpo`), measured over run1.
# The pass is the last of these a name carries; tokens after it (`Skin`, `FT`) modify it.
PASS_TOKENS = {"Gbuf", "GBufDpt", "Dpt", "DptA", "Velo", "Fwd", "FwdSdw", "FwdDpt", "FwdSsr",
               "FurDir", "Pnt", "PntCol", "Spec"}
# Member names that would mark a wind or sway mechanism, matched case-insensitively.
WIND_WORDS = re.compile(r"wind|sway|gust|bend|flutter|shake|wave|swing|vtxani|vertexani|"
                        r"force|tree|grass|leaf|branch|cloth|wobble|jiggle|anim", re.I)


class DumpError(Exception):
    pass


def u32(data: bytes, at: int) -> int:
    if at < 0 or at + 4 > len(data):
        raise DumpError(f"u32 read at {at} past {len(data)} bytes")
    return struct.unpack_from("<I", data, at)[0]


def cstring(data: bytes, at: int) -> str:
    end = data.index(b"\0", at)
    return data[at:end].decode("utf-8", "strict")


def parts_of(blob: bytes) -> dict[bytes, bytes]:
    """The container's parts by four-character code; the first of a repeated code wins."""
    if blob[:4] != b"DXBC":
        raise DumpError("not a DXBC container")
    total = u32(blob, 24)
    if total != len(blob):
        raise DumpError(f"declared size {total} is not the file size {len(blob)}")
    found: dict[bytes, bytes] = {}
    for index in range(u32(blob, 28)):
        offset = u32(blob, 32 + 4 * index)
        size = u32(blob, offset + 4)
        if offset + 8 + size > len(blob):
            raise DumpError("a part runs past the container")
        found.setdefault(blob[offset:offset + 4], blob[offset + 8:offset + 8 + size])
    return found


def program_kind(parts: dict[bytes, bytes]) -> tuple[str, int, int]:
    """(kind, major, minor) from the DXIL program header or the SM5 version token."""
    for code in (b"DXIL", b"SHEX", b"SHDR"):
        if code in parts:
            version = u32(parts[code], 0)
            return KIND_NAMES.get(version >> 16, f"kind{version >> 16}"), (version >> 4) & 0xF, \
                version & 0xF
    raise DumpError("no program part")


@dataclass(frozen=True)
class Element:
    name: str
    index: int
    register: int
    mask: int
    system_value: int
    component: int
    stream: int = 0
    rw: int = 0

    def label(self) -> str:
        sv = SYSTEM_VALUES.get(self.system_value, str(self.system_value))
        mask = "".join(c for bit, c in zip((1, 2, 4, 8), "xyzw") if self.mask & bit)
        return f"{self.name}{self.index}@{self.register}.{mask}" + (f"/{sv}" if sv else "") + \
            ("" if self.component == 3 else f"/{COMPONENT_TYPES.get(self.component, '?')}")


def signature(part: bytes) -> list[Element]:
    count, first = u32(part, 0), u32(part, 4)
    out = []
    for n in range(count):
        at = first + 32 * n
        stream, name_at, index, sv, comp, reg = struct.unpack_from("<6I", part, at)
        mask, rw = part[at + 24], part[at + 25]
        out.append(Element(cstring(part, name_at), index, reg, mask, sv, comp, stream, rw))
    return out


@dataclass(frozen=True)
class PsvElement:
    """One `PSVSignatureElement0`: where a DXIL signature element sits, in element-ID order."""
    name: str
    index: int
    start_row: int
    start_col: int
    rows: int
    allocated: bool


@dataclass
class Psv:
    stage: int | None
    stage_info: bytes
    binds: list[tuple[str, int, int, int]]
    max_vertex_count: int | None = None
    # Inputs, outputs and patch-constant elements in DXIL element-ID order, which is the ID
    # `loadInput` and `storeOutput` name. It is not the order ISG1 and OSG1 list them in: those are
    # sorted by register, so a packed element such as `TEXCOORD9@5.yzw` sits between two elements
    # whose IDs come after it.
    elements: dict[str, list[PsvElement]] = field(default_factory=dict)


def psv0(part: bytes) -> Psv:
    runtime_size = u32(part, 0)
    runtime = part[4:4 + runtime_size]
    at = 4 + runtime_size
    count = u32(part, at)
    at += 4
    binds = []
    if count:
        record = u32(part, at)
        at += 4
        for n in range(count):
            rtype, space, low, high = struct.unpack_from("<4I", part, at + n * record)
            binds.append((PSV_RESOURCE.get(rtype, str(rtype)), space, low, high))
        at += count * record
    stage = runtime[24] if runtime_size >= 36 else None
    max_vertices = struct.unpack_from("<H", runtime, 26)[0] if runtime_size >= 36 else None
    elements: dict[str, list[PsvElement]] = {"in": [], "out": [], "pc": []}
    if runtime_size >= 36:
        # `PSVRuntimeInfo1`: SigInputElements, SigOutputElements, SigPatchConstOrPrimElements at
        # bytes 28-30, then the string table, the semantic index table and the element records.
        counts = (("in", runtime[28]), ("out", runtime[29]), ("pc", runtime[30]))
        strings_size = u32(part, at)
        strings = part[at + 4:at + 4 + strings_size]
        at += 4 + strings_size
        index_count = u32(part, at)
        indexes = struct.unpack_from(f"<{index_count}I", part, at + 4)
        at += 4 + 4 * index_count
        if any(n for _, n in counts):
            record = u32(part, at)
            at += 4
            for key, n in counts:
                for _ in range(n):
                    name_at, index_at, rows, start_row, cols_and_start = \
                        struct.unpack_from("<IIBBB", part, at)
                    at += record
                    elements[key].append(PsvElement(
                        cstring(strings, name_at), indexes[index_at], start_row,
                        (cols_and_start >> 4) & 3, rows, bool(cols_and_start & 0x40)))
    return Psv(stage, runtime[:16], binds, max_vertices, elements)


def by_element_id(listed: list[Element], placed: list[PsvElement]) -> list[Element | None]:
    """`listed` (ISG1 or OSG1 order) rearranged into element-ID order: each PSV0 element is the
    signature row that starts at its register and column. An unallocated element has no register
    and maps to the listed element of the same semantic, or to `None`."""
    out: list[Element | None] = []
    for element in placed:
        if element.allocated:
            hits = [e for e in listed if e.register == element.start_row and e.mask
                    and (e.mask & -e.mask).bit_length() - 1 == element.start_col]
        else:
            hits = [e for e in listed if e.name.upper() == element.name.upper()
                    and e.index == element.index]
        out.append(hits[0] if len(hits) == 1 else None)
    return out


@dataclass
class RootSignature:
    version: int
    flags: int
    params: list[dict]
    static_samplers: list[dict]

    def flag_names(self) -> list[str]:
        return [name for bit, name in RS_FLAGS if self.flags & bit]

    def dwords(self) -> int:
        cost = {"table": 1, "cbv": 2, "srv": 2, "uav": 2}
        return sum(p.get("count", 0) if p["type"] == "constants" else cost[p["type"]]
                   for p in self.params)

    def summary(self) -> dict:
        by_visibility = collections.Counter(p["visibility"] for p in self.params)
        return {"version": f"1.{self.version - 1}", "flags": hex(self.flags),
                "flag_names": self.flag_names(), "parameters": len(self.params),
                "dwords": self.dwords(), "static_samplers": len(self.static_samplers),
                "visibility": dict(sorted(by_visibility.items())),
                "params": self.params}


def root_signature(blob: bytes) -> RootSignature:
    version, count, params_at, samplers, samplers_at, flags = struct.unpack_from("<6I", blob, 0)
    params = []
    for n in range(count):
        ptype, visibility, payload = struct.unpack_from("<3I", blob, params_at + 12 * n)
        entry = {"type": PARAM_TYPES.get(ptype, str(ptype)),
                 "visibility": VISIBILITY.get(visibility, str(visibility))}
        if ptype == 0:
            ranges, ranges_at = struct.unpack_from("<2I", blob, payload)
            size = 20 if version == 1 else 24
            entry["ranges"] = []
            for r in range(ranges):
                rtype, num, base, space = struct.unpack_from("<4I", blob, ranges_at + size * r)
                last = "unbounded" if num == 0xFFFFFFFF else base + num - 1
                entry["ranges"].append(f"{RANGE_TYPES.get(rtype, '?')}{base}-{last} space{space}")
        elif ptype == 1:
            reg, space, values = struct.unpack_from("<3I", blob, payload)
            entry.update(register=f"b{reg} space{space}", count=values)
        else:
            reg, space = struct.unpack_from("<2I", blob, payload)
            prefix = {2: "b", 3: "t", 4: "u"}[ptype]
            entry["register"] = f"{prefix}{reg} space{space}"
        params.append(entry)
    sampler_size = 56 if version >= 3 else 52
    sampler_list = []
    for n in range(samplers):
        fields = struct.unpack_from("<13I", blob, samplers_at + sampler_size * n)
        sampler_list.append({"register": f"s{fields[10]} space{fields[11]}",
                             "visibility": VISIBILITY.get(fields[12], str(fields[12]))})
    return RootSignature(version, flags, params, sampler_list)


def debug_name(parts: dict[bytes, bytes]) -> str:
    part = parts.get(b"ILDN")
    if not part:
        return ""
    length = struct.unpack_from("<H", part, 2)[0]
    return part[4:4 + length].decode("utf-8", "strict")


def short_name(full: str) -> str:
    stem = re.split(r"[\\/]", full)[-1]
    return re.sub(r"\.\w+\.pdb$", "", stem)


def pass_of(name: str) -> tuple[str, tuple[str, ...]]:
    """(pass, modifiers) from a material shader's debug name, or ("", ()) for a non-material one."""
    base = re.sub(r"@.*$", "", short_name(name))
    tokens = [t for t in re.sub(r"\[[^\]]*\]", "", base).split("_") if t]
    for at in range(len(tokens) - 1, 0, -1):
        if tokens[at] in PASS_TOKENS:
            return tokens[at], tuple(tokens[at + 1:])
    return "", ()


def family_of(name: str) -> str:
    """The material family prefix of a debug name: `C`, `M`, `S`, `P`, `CS`, or a GX filter dir."""
    stem = short_name(name)
    match = re.match(r"([A-Za-z]+)\[", stem)
    if match:
        return match.group(1)
    if "SpeedTree" in name:
        return "SpeedTree"
    directory = re.split(r"[\\/]", name)
    return directory[-2] if len(directory) > 1 else "?"


@dataclass
class Shader:
    hash: str
    kind: str
    model: str
    name: str
    isg: list[Element]
    osg: list[Element]
    psg: list[Element]
    psv: Psv | None
    rts0: bytes | None
    size: int
    reads: dict = field(default_factory=dict)
    # ISG1 and OSG1 rows by DXIL element ID, the index `loadInput` and `storeOutput` use.
    isg_by_id: list[Element | None] = field(default_factory=list)
    osg_by_id: list[Element | None] = field(default_factory=list)

    @property
    def short(self) -> str:
        return short_name(self.name)


def load_shader(path: Path) -> Shader:
    blob = path.read_bytes()
    parts = parts_of(blob)
    kind, major, minor = program_kind(parts)
    sig = {code: signature(parts[code]) if code in parts else []
           for code in (b"ISG1", b"OSG1", b"PSG1")}
    psv = psv0(parts[b"PSV0"]) if b"PSV0" in parts else None
    shader = Shader(path.name.split(".")[0], kind, f"{major}.{minor}", debug_name(parts),
                    sig[b"ISG1"], sig[b"OSG1"], sig[b"PSG1"], psv, parts.get(b"RTS0"),
                    len(blob))
    if psv is not None:
        shader.isg_by_id = by_element_id(shader.isg, psv.elements.get("in", []))
        shader.osg_by_id = by_element_id(shader.osg, psv.elements.get("out", []))
    return shader


def fnv1_64(data: bytes) -> int:
    value = 0xCBF29CE484222325
    for byte in data:
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        value ^= byte
    return value


@dataclass
class Pipeline:
    stages: dict[str, str]
    # The second word of chunk type 5, which equals the FNV-1 64 name vkd3d gives the dumped `.rs`
    # blob of the root signature the pipeline was created with (checked against the dump).
    root_signature: str = ""


def read_cache(blob: bytes) -> list[Pipeline]:
    """Pipelines in a vkd3d-proton stream archive: 48 bytes of header, then entries of
    `{u64 hash, u64 check, u32 size, u32 type}` and `size` bytes. A pipeline blob starts `VKB4`
    with a 48-byte header, then chunks of `{u16 type, u16 stage, u32 size}` padded to 8 bytes;
    chunk type 4 starts with the stage's container hash. The layout was read off the file and is
    checked by `check_cache_against_dump`, which compares every stage bit with the program kind of
    the container it names."""
    if blob[:4] != b"VKS\x04":
        raise DumpError("not a vkd3d-proton stream archive")
    at, pipelines = 48, []
    while at + 24 <= len(blob):
        _, _, size, _ = struct.unpack_from("<QQII", blob, at)
        payload = blob[at + 24:at + 24 + size]
        if len(payload) != size:
            raise DumpError(f"entry at {at} runs past the file")
        at += 24 + size
        if payload[:4] != b"VKB\x04":
            continue
        stages, chunk, root = {}, 48, ""
        while chunk + 8 <= len(payload):
            ctype, stage, csize = struct.unpack_from("<HHI", payload, chunk)
            if ctype == 4:
                stages[VK_STAGE.get(stage, hex(stage))] = \
                    "%016x" % struct.unpack_from("<Q", payload, chunk + 8)[0]
            elif ctype == 5 and csize >= 16:
                root = "%016x" % struct.unpack_from("<Q", payload, chunk + 16)[0]
            chunk += 8 + (csize + 7) // 8 * 8
        pipelines.append(Pipeline(stages, root))
    if at != len(blob):
        raise DumpError(f"{len(blob) - at} bytes after the last entry")
    return pipelines


PIPELINE_STAGES = ("vs", "hs", "ds", "gs", "ps")


def pipeline_rows(pipelines: list[Pipeline]) -> list[str]:
    """One tab-separated row per graphics pipeline: root signature, then the container hash of
    each stage in `PIPELINE_STAGES` order, `-` where the pipeline has none. A compute pipeline has
    no graphics stage and is a row of dashes, so row numbers stay the cache's entry numbers."""
    return ["\t".join([pipeline.root_signature or "-"]
                      + [pipeline.stages.get(stage, "-") for stage in PIPELINE_STAGES])
            for pipeline in pipelines]


def check_cache_against_dump(pipelines: list[Pipeline], shaders: dict[str, Shader]) -> dict:
    agree = disagree = missing = 0
    for pipeline in pipelines:
        for stage, hash_ in pipeline.stages.items():
            shader = shaders.get(hash_)
            if shader is None:
                missing += 1
            elif shader.kind == stage:
                agree += 1
            else:
                disagree += 1
    return {"stage_hashes_matching_kind": agree, "mismatched": disagree, "not_in_dump": missing}


# ---- dxc -dumpbin reading ---------------------------------------------------------------------

@dataclass
class Member:
    name: str
    offset: int
    count: int
    children: list[Member]
    span: int = 0

    @property
    def stride(self) -> int:
        if self.count <= 1:
            return self.span
        return -(-(self.span // self.count) // 16) * 16 if self.span % self.count else \
            self.span // self.count


def finish_spans(members: list[Member], end: int) -> None:
    for n, member in enumerate(members):
        member.span = (members[n + 1].offset if n + 1 < len(members) else end) - member.offset
        if member.children:
            finish_spans(member.children, member.stride)


MEMBER_LINE = re.compile(r"^;\s+(?:[\w:]+\s+)*?([\w.]+)\s+(\w+)(?:\[(\d+)\])?;\s*;\s*Offset:\s*(\d+)")
CLOSE_LINE = re.compile(r"^;\s+\}\s*(\w+)(?:\[(\d+)\])?;+\s*;\s*Offset:\s*(\d+)(?:\s+Size:\s*(\d+))?")


def parse_buffers(text: str) -> dict[str, tuple[list[Member], int]]:
    """Each cbuffer's members and size, from the `Buffer Definitions` block."""
    buffers: dict[str, tuple[list[Member], int]] = {}
    block = text.split("; Buffer Definitions:", 1)
    if len(block) < 2:
        return buffers
    lines = block[1].split("; Resource Bindings:", 1)[0].splitlines()
    stack: list[list[Member]] = []
    current = None
    for line in lines:
        opened = re.match(r"^;\s+(cbuffer|tbuffer)\s+(\w+)", line)
        if opened:
            current, stack = opened.group(2), []
            continue
        if re.match(r"^;\s+struct\s", line):
            stack.append([])
            continue
        closed = CLOSE_LINE.match(line)
        if closed and stack:
            members = stack.pop()
            name, count, offset, size = closed.groups()
            if not stack:
                if current is not None and size is not None:
                    finish_spans(members, int(size))
                    buffers[current] = (members, int(size))
            else:
                stack[-1].append(Member(name, int(offset), int(count or 1), members))
            continue
        member = MEMBER_LINE.match(line)
        if member and stack:
            _, name, count, offset = member.groups()
            stack[-1].append(Member(name, int(offset), int(count or 1), []))
    return buffers


def label_of(member: Member, prefix: str) -> str:
    return prefix + member.name + ("[]" if member.count > 1 else "")


def resolve(members: list[Member], byte: int, prefix: str = "", width: int = 16) -> list[str]:
    """The members covering the `width` bytes at `byte`."""
    hits = []
    for member in members:
        if member.offset + member.span <= byte or member.offset >= byte + width:
            continue
        label = label_of(member, prefix)
        if member.children and member.stride:
            inner = (byte - member.offset) % member.stride if byte >= member.offset else 0
            hits += resolve(member.children, inner, label + ".", width) or [label]
        else:
            hits.append(label)
    return hits


def strided(members: list[Member], stride: int, byte: int, prefix: str = "",
            width: int = 16) -> list[str]:
    """Members an index of the form `dynamic * stride + byte` reads, taking the dynamic part as an
    element index of the array that `byte` lands in."""
    hits = []
    for member in members:
        if not member.offset <= byte < member.offset + member.span:
            continue
        label = label_of(member, prefix)
        if member.count > 1 and member.stride == stride:
            inner = (byte - member.offset) % stride
            hits += resolve(member.children, inner, label + ".", width) if member.children \
                else [label]
        elif member.children and member.stride:
            inner = (byte - member.offset) % member.stride
            hits += strided(member.children, stride, inner, label + ".", width)
    return hits


DEF_LINE = re.compile(r"^\s*(%[\w.]+)\s*=\s*(.*)$")
BINDING_LINE = re.compile(r"^;\s+(\S+)\s+(cbuffer|texture|sampler|UAV)\s+(\S+)\s+(\S+)\s+"
                          r"(\w+)\s+(\S+)\s+(\d+)")


def index_form(defs: dict[str, str], operand: str, depth: int = 0) -> tuple[int | None, int | None]:
    """(stride, constant) of a cbuffer register index: `constant` alone for a literal, a stride
    for `x * stride + constant`, and (None, None) when it is anything else."""
    operand = operand.strip()
    if re.fullmatch(r"-?\d+", operand):
        return 0, int(operand)
    if depth > 8 or operand not in defs:
        return None, None
    text = defs[operand]
    mul = re.match(r"mul (?:nuw |nsw )*i32 (%[\w.]+|\d+), (%[\w.]+|\d+)", text)
    if mul:
        a, b = mul.groups()
        if re.fullmatch(r"\d+", b):
            return int(b), 0
        if re.fullmatch(r"\d+", a):
            return int(a), 0
        return None, None
    shl = re.match(r"shl (?:nuw |nsw )*i32 (%[\w.]+), (\d+)", text)
    if shl:
        return 1 << int(shl.group(2)), 0
    add = re.match(r"(?:add|or) (?:nuw |nsw |disjoint )*i32 (%[\w.]+|-?\d+), (%[\w.]+|-?\d+)", text)
    if add:
        left, right = (index_form(defs, x, depth + 1) for x in add.groups())
        if left[0] is not None and right[0] is not None:
            if left[0] and right[0]:
                return None, None
            return (left[0] or right[0]), left[1] + right[1]
        # An index of unknown form plus a constant: a register-granular array from the constant.
        for known, unknown in ((left, right), (right, left)):
            if known[0] == 0 and unknown[0] is None:
                return 1, known[1]
    return None, None


def analyse_disassembly(text: str) -> dict:
    """Resources a shader binds and the cbuffer members it reads, from `dxc -dumpbin` text."""
    buffers = parse_buffers(text)
    bindings, by_id = [], {}
    table = text.split("; Resource Bindings:", 1)
    rows = []
    if len(table) == 2:
        for row in table[1].splitlines()[1:]:
            if not row.startswith(";") or row.startswith("; ViewId"):
                break
            rows.append(row)
    for row in rows:
        m = BINDING_LINE.match(row)
        if m:
            name, kind, fmt, dim, rid, bind, count = m.groups()
            bindings.append({"name": name, "kind": kind, "format": fmt, "dim": dim,
                             "bind": bind, "count": int(count)})
            by_id[rid] = name
    defs: dict[str, str] = {}
    handles: dict[str, str] = {}
    for line in text.splitlines():
        m = DEF_LINE.match(line)
        if not m:
            continue
        defs[m.group(1)] = m.group(2)
        handle = re.search(r"@dx\.op\.createHandle\(i32 57, i8 (\d), i32 (\d+), ", m.group(2))
        if handle:
            prefix = {"0": "T", "1": "U", "2": "CB", "3": "S"}[handle.group(1)]
            handles[m.group(1)] = by_id.get(prefix + handle.group(2), prefix + handle.group(2))
    # The components each load's result is taken apart into, so a member is counted as read only
    # when its own 4-byte component is extracted, not whenever its 16-byte register is loaded.
    components: dict[str, set[int]] = collections.defaultdict(set)
    for value, component in re.findall(r"extractvalue %dx\.types\.CBufRet\.\w+ (%[\w.]+), (\d)",
                                       text):
        components[value].add(int(component))
    reads: dict[str, set[str]] = collections.defaultdict(set)
    unresolved = collections.Counter()
    # What each value is made from: a cbuffer member for an extracted component, an input
    # signature row for `loadInput`, a resource for a load or sample through its handle.
    seeds: dict[str, set[str]] = collections.defaultdict(set)
    per_component: dict[tuple[str, int], list[str]] = {}
    for line in text.splitlines():
        load = re.search(r"@dx\.op\.cbufferLoadLegacy\.\w+\(i32 59, %dx\.types\.Handle (%[\w.]+),"
                         r" i32 ([^)]+)\)", line)
        if not load:
            continue
        result = DEF_LINE.match(line)
        taken = sorted(components.get(result.group(1), ())) if result else []
        windows = [(4 * c, 4) for c in taken] or [(0, 16)]
        buffer = handles.get(load.group(1), "?")
        members, _ = buffers.get(buffer, ([], 0))
        stride, constant = index_form(defs, load.group(2))
        hit = []
        for at, width in windows:
            if stride == 0 and constant is not None:
                found = resolve(members, 16 * constant + at, width=width)
            elif stride and constant is not None:
                found = strided(members, 16 * stride, 16 * constant + at, width=width)
            else:
                found = []
            hit += found
            if result and width == 4:
                per_component[(result.group(1), at // 4)] = \
                    [f"{buffer}.{m}" for m in found] or [f"{buffer}.<dynamic>"]
        if hit:
            reads[buffer].update(hit)
        else:
            unresolved[buffer] += 1
            reads[buffer].add("<dynamic>")
    for value, rhs in defs.items():
        extract = re.match(r"extractvalue %dx\.types\.CBufRet\.\w+ (%[\w.]+), (\d)", rhs)
        if extract:
            seeds[value].update(per_component.get((extract.group(1), int(extract.group(2))), ()))
        loaded = re.search(r"@dx\.op\.loadInput\.\w+\(i32 4, i32 (\d+), ", rhs)
        if loaded:
            seeds[value].add(f"in#{loaded.group(1)}")
        through = re.search(r"@dx\.op\.(\w+)\.\w+\(i32 \d+, %dx\.types\.Handle (%[\w.]+)", rhs)
        if through and not through.group(1).startswith("cbufferLoad"):
            seeds[value].add(f"res:{handles.get(through.group(2), '?')}")
    outputs: dict[int, set[str]] = collections.defaultdict(set)
    memo: dict[str, frozenset[str]] = {}
    operands_of = {value: [o for o in re.findall(r"%[\w.]+", rhs) if o in defs and o != value]
                   for value, rhs in defs.items()}

    def made_of(value: str) -> frozenset[str]:
        # Iterative post-order over the operand graph; a value is expanded once, and a phi that
        # reaches itself through a loop sees what was found so far rather than recursing.
        entered: set[str] = set()
        stack = [(value, False)]
        while stack:
            current, expanded = stack.pop()
            if current in memo:
                continue
            if not expanded:
                if current in entered:
                    continue
                entered.add(current)
                stack.append((current, True))
                stack.extend((o, False) for o in operands_of.get(current, ())
                             if o not in memo and o not in entered)
                continue
            found = set(seeds.get(current, ()))
            for o in operands_of.get(current, ()):
                found |= memo.get(o, frozenset())
            memo[current] = frozenset(found)
        return memo.get(value, frozenset())

    # The value each output component was last stored with, so an output that is a copy of
    # another (the same SSA value in every component) can be told from one computed separately.
    stored: dict[int, dict[str, str]] = collections.defaultdict(dict)
    for sig, row, col, value in re.findall(r"@dx\.op\.storeOutput\.\w+\(i32 5, i32 (\d+), "
                                           r"i32 ([^,]+), i8 ([^,]+), \w+ ([^)]+)\)", text):
        stored[int(sig)][f"{row}.{col}"] = value.strip()
        if value.strip() in defs:
            outputs[int(sig)] |= made_of(value.strip())
    handle_ops = sorted(set(re.findall(r"@dx\.op\.(createHandle\w*|annotateHandle)", text)))
    return {"bindings": bindings,
            "reads": {k: sorted(v) for k, v in sorted(reads.items())},
            "unresolved_loads": dict(unresolved),
            "handle_ops": handle_ops,
            "outputs": {k: sorted(v) for k, v in sorted(outputs.items())},
            "stored": {k: v for k, v in sorted(stored.items())},
            "members": {k: sorted(flatten(v[0])) for k, v in buffers.items()}}


def element_of(by_id: list[Element | None], sig: int) -> Element | None:
    return by_id[sig] if 0 <= sig < len(by_id) else None


def input_label(s: Shader, source: str) -> str:
    """`in:<semantic>` for an `in#<element ID>` source; any other source as it is."""
    if not source.startswith("in#"):
        return source
    element = element_of(s.isg_by_id, int(source[3:]))
    return f"in:{element.name}{element.index}" if element else source


def flatten(members: list[Member], prefix: str = "") -> list[str]:
    out = []
    for member in members:
        label = prefix + member.name + ("[]" if member.count > 1 else "")
        out += flatten(member.children, label + ".") if member.children else [label]
    return out


def disassemble(dxc: Path, path: Path) -> str:
    done = subprocess.run([str(dxc), "-dumpbin", str(path)], capture_output=True, text=True,
                          timeout=DUMPBIN_SECONDS, check=False)
    if done.returncode != 0:
        raise DumpError(f"dxc -dumpbin {path.name}: {done.stderr.strip()[:200]}")
    return done.stdout


# ---- the census ---------------------------------------------------------------------------------

def default_dump() -> Path:
    env = os.environ.get("ER_VKD3D_DUMP_ROOT")
    if env:
        return Path(env)
    main = REPO
    if ".worktrees" in REPO.parts:
        main = Path(*REPO.parts[:REPO.parts.index(".worktrees")])
    return main / DUMP_RELATIVE


def shape_key(elements: list[Element]) -> str:
    return " ".join(e.label() for e in elements)


def census(dump: Path, cache: Path | None, dxc: Path | None, sections: set[str]) -> dict:
    files = sorted(dump.iterdir())
    shaders: dict[str, Shader] = {}
    errors = {}
    for path in files:
        if path.suffix in (".dxil",):
            try:
                shaders[path.name.split(".")[0]] = load_shader(path)
            except (DumpError, ValueError, struct.error) as error:
                errors[path.name] = str(error)
    report: dict = {"dump": str(dump), "files": dict(collections.Counter(
        "".join(p.suffixes[-1:]) for p in files)), "parse_errors": errors}
    names_match = sum(1 for p in files if p.suffix == ".dxil"
                      and "%016x" % fnv1_64(p.read_bytes()) == p.name.split(".")[0])
    report["file_name_is_fnv1_64_of_bytes"] = f"{names_match}/{len(shaders) + len(errors)}"

    by_kind = collections.Counter(s.kind for s in shaders.values())
    psv_agree = sum(1 for s in shaders.values() if s.psv and s.psv.stage is not None
                    and KIND_NAMES.get(s.psv.stage) == s.kind)
    psv_present = sum(1 for s in shaders.values() if s.psv and s.psv.stage is not None)
    report["stages"] = {"by_program_kind": dict(by_kind.most_common()),
                        "psv0_stage_agrees": f"{psv_agree}/{psv_present}",
                        "models": dict(collections.Counter(f"{s.kind}_{s.model}"
                                                           for s in shaders.values())),
                        "named": sum(1 for s in shaders.values() if s.name)}
    # The SM5 containers: the game's own few (each embedding an `RTS0`) and anything compiled at
    # run time from HLSL, which vkd3d also dumps under a `vkd3d-shader-*` name.
    dxbc = collections.Counter()
    for path in files:
        if path.suffix != ".dxbc":
            continue
        sm5 = parts_of(path.read_bytes())
        named = "vkd3d-named" if path.name.startswith("vkd3d-shader") else "hash-named"
        dxbc[f"{program_kind(sm5)[0]} {'rts0' if b'RTS0' in sm5 else 'no-rts0'} {named}"] += 1
    report["stages"]["dxbc"] = dict(dxbc.most_common())

    # Root signatures.
    rs_files = {p.name: root_signature(p.read_bytes()).summary() for p in files
                if p.suffix == ".rs"}
    embedded = collections.defaultdict(list)
    for s in shaders.values():
        if s.rts0:
            embedded["%016x" % fnv1_64(s.rts0)].append(s)
    rs_embedded = {}
    for key, users in embedded.items():
        summary = root_signature(users[0].rts0).summary()
        summary["used_by"] = dict(collections.Counter(u.kind for u in users))
        summary["same_bytes_as_dumped"] = [n for n, p in ((p.name, p) for p in files
                                                          if p.suffix == ".rs")
                                           if fnv1_64(p.read_bytes()) == int(key, 16)]
        rs_embedded[key] = summary
    report["rootsig"] = {"dumped": rs_files, "embedded": rs_embedded,
                         "shaders_without_rts0": dict(collections.Counter(
                             s.kind for s in shaders.values() if not s.rts0))}

    vs = [s for s in shaders.values() if s.kind == "vs"]
    report["vs"] = vertex_section(vs)
    report["hs_ds_gs"] = tess_geometry_section(shaders)

    pipelines: list[Pipeline] = []
    if cache and cache.exists():
        pipelines = read_cache(cache.read_bytes())
        report["pipelines"] = pipeline_section(pipelines, shaders)
    report["passes"] = pass_section(shaders, pipelines)

    if dxc and "reads" in sections:
        report["reads"] = reads_section(dump, dxc, shaders, pipelines)
    return report


def vertex_section(vs: list[Shader]) -> dict:
    shapes = collections.Counter(shape_key(s.osg) for s in vs)
    position_outputs = collections.Counter()
    position_examples = {}
    for s in vs:
        for e in s.osg:
            if e.name.upper() == "POSITION" and e.system_value == 0:
                key = f"POSITION{e.index}@{e.register}"
                position_outputs[key] += 1
                position_examples.setdefault(key, s.short)
    sv_inputs = collections.Counter()
    for s in vs:
        for e in s.isg:
            if e.system_value:
                sv_inputs[SYSTEM_VALUES.get(e.system_value, str(e.system_value))] += 1
    sv_position_register = collections.Counter(
        next((e.register for e in s.osg if e.system_value == 1), -1) for s in vs)
    no_position = [s.short for s in vs if not any(e.system_value == 1 for e in s.osg)]
    binds = collections.Counter()
    for s in vs:
        for b in (s.psv.binds if s.psv else []):
            binds[f"{b[0]} space{b[1]} {b[2]}-{b[3]}"] += 1
    stream = sum(1 for s in vs if any(e.stream for e in s.osg))
    inputs = collections.Counter(shape_key([e for e in s.isg if not e.system_value]) for s in vs)
    return {"count": len(vs),
            "osg_shapes_distinct": len(shapes),
            "osg_shapes_top": shapes.most_common(25),
            "outputs_named_POSITION": dict(position_outputs.most_common()),
            "outputs_named_POSITION_examples": position_examples,
            "sv_position_register": dict(sv_position_register),
            "without_sv_position": no_position,
            "system_value_inputs": dict(sv_inputs),
            "vertex_input_layouts_distinct": len(inputs),
            "vertex_input_layouts_top": inputs.most_common(12),
            "psv0_binds": dict(binds.most_common()),
            "outputs_on_a_nonzero_stream": stream}


def tess_geometry_section(shaders: dict[str, Shader]) -> dict:
    out = {}
    for kind in ("hs", "ds", "gs"):
        rows = []
        for s in shaders.values():
            if s.kind != kind:
                continue
            info = s.psv.stage_info if s.psv else b"\0" * 16
            row = {"hash": s.hash, "name": s.short}
            if kind == "hs":
                row.update(zip(("in_cp", "out_cp", "domain", "out_prim"),
                               struct.unpack_from("<4I", info)))
            elif kind == "ds":
                row.update(in_cp=u32(info, 0), position=info[4], domain=u32(info, 8))
            else:
                row.update(input_prim=u32(info, 0), out_topology=u32(info, 4),
                           stream_mask=u32(info, 8), position=info[12],
                           max_vertices=s.psv.max_vertex_count if s.psv else None)
                row["osg"] = shape_key(s.osg)
                row["isg"] = shape_key(s.isg)
            rows.append(row)
        out[kind] = sorted(rows, key=lambda r: r["name"])
    return out


def pipeline_section(pipelines: list[Pipeline], shaders: dict[str, Shader]) -> dict:
    sets = collections.Counter("+".join(sorted(p.stages, key="vs hs ds gs ps cs".split().index))
                               for p in pipelines)
    named = collections.defaultdict(collections.Counter)
    for p in pipelines:
        key = "+".join(sorted(p.stages, key="vs hs ds gs ps cs".split().index))
        if key in ("vs+ps", "cs"):
            continue
        vs = shaders.get(p.stages.get("vs", ""))
        ps = shaders.get(p.stages.get("ps", ""))
        extra = [shaders.get(p.stages[k]) for k in ("hs", "ds", "gs") if k in p.stages]
        label = " | ".join(x.short if x else "?" for x in [vs, *extra, ps] if x is not None)
        named[key][label] += 1
    vs_only = collections.Counter()
    for p in pipelines:
        if set(p.stages) == {"vs"}:
            vs = shaders.get(p.stages["vs"])
            vs_only[pass_of(vs.name)[0] or family_of(vs.name) if vs else "?"] += 1
    by_root = collections.defaultdict(collections.Counter)
    for p in pipelines:
        key = "+".join(sorted(p.stages, key="vs hs ds gs ps cs".split().index))
        by_root[p.root_signature][key] += 1
    root_examples = collections.defaultdict(collections.Counter)
    for p in pipelines:
        if len(by_root[p.root_signature]) and sum(by_root[p.root_signature].values()) < 200:
            names = [shaders[h].short if h in shaders else h for h in p.stages.values()]
            root_examples[p.root_signature][" | ".join(names)] += 1
    return {"total": len(pipelines),
            "stage_sets": dict(sets.most_common()),
            "by_root_signature": {k: dict(v) for k, v in by_root.items()},
            "root_signature_examples": {k: v.most_common(12) for k, v in root_examples.items()},
            "check": check_cache_against_dump(pipelines, shaders),
            "distinct_shaders_used": len({h for p in pipelines for h in p.stages.values()}),
            "vs_only_by_pass": dict(vs_only.most_common()),
            "with_tessellation_or_gs": {k: v.most_common() for k, v in named.items()
                                        if k not in ("vs",)}}


def pass_section(shaders: dict[str, Shader], pipelines: list[Pipeline]) -> dict:
    per = collections.defaultdict(collections.Counter)
    modifiers = collections.Counter()
    for s in shaders.values():
        pass_, mods = pass_of(s.name)
        per[s.kind][pass_ or "(non-material)"] += 1
        for m in mods:
            modifiers[(s.kind, m)] += 1
    pairs = collections.Counter()
    vs_pass_ps_pass = collections.Counter()
    ps_per_vs = collections.defaultdict(set)
    vs_per_ps = collections.defaultdict(set)
    for p in pipelines:
        vs = shaders.get(p.stages.get("vs", ""))
        ps = shaders.get(p.stages.get("ps", ""))
        if vs is None:
            continue
        vp = pass_of(vs.name)[0] or "(non-material)"
        pp = (pass_of(ps.name)[0] or "(non-material)") if ps else "(no PS)"
        vs_pass_ps_pass[f"{vp} -> {pp}"] += 1
        if ps:
            ps_per_vs[vs.hash].add(ps.hash)
            vs_per_ps[ps.hash].add(vs.hash)
        pairs[(vp, pp)] += 1
    fanout = collections.Counter(min(len(v), 10) for v in ps_per_vs.values())
    fanin = collections.Counter(min(len(v), 10) for v in vs_per_ps.values())
    velocity = {}
    for s in shaders.values():
        if pass_of(s.name)[0] == "Velo":
            key = f"{s.kind}: " + shape_key(s.osg if s.kind == "vs" else s.osg)
            velocity[key] = velocity.get(key, 0) + 1
    gbuf_ps_targets = collections.Counter(
        sum(1 for e in s.osg if e.system_value == 64) for s in shaders.values()
        if s.kind == "ps" and pass_of(s.name)[0] == "Gbuf")
    targets = {}
    for kind_pass in ("Gbuf", "GBufDpt", "Dpt", "DptA", "Velo", "Fwd", "FwdSdw", "FwdDpt",
                      "FwdSsr", "Spec", "FurDir", "Pnt", "PntCol"):
        counts = collections.Counter(
            shape_key(s.osg) for s in shaders.values()
            if s.kind == "ps" and pass_of(s.name)[0] == kind_pass)
        if counts:
            targets[kind_pass] = counts.most_common(4)
    return {"by_stage": {k: dict(v.most_common()) for k, v in per.items()},
            "modifiers": {f"{k}:{m}": n for (k, m), n in modifiers.most_common(20)},
            "pipelines_vs_pass_to_ps_pass": dict(vs_pass_ps_pass.most_common()),
            "distinct_ps_per_vs": dict(sorted(fanout.items())),
            "distinct_vs_per_ps": dict(sorted(fanin.items())),
            "velocity_shapes": velocity,
            "gbuf_ps_render_targets": dict(gbuf_ps_targets),
            "ps_output_shapes_by_pass": targets}


def reads_section(dump: Path, dxc: Path, shaders: dict[str, Shader],
                  pipelines: list[Pipeline]) -> dict:
    wanted = [s for s in shaders.values() if s.kind in ("vs", "hs", "ds", "gs")]
    wanted += [s for s in shaders.values() if s.kind == "ps" and pass_of(s.name)[0] == "Velo"]
    wanted += [s for s in shaders.values() if s.kind == "ps"
               and re.search(r"Velocity|MotionVector|MotionBlur|TempAA|TAA", s.name)]

    def one(s: Shader) -> tuple[str, dict | str]:
        try:
            return s.hash, analyse_disassembly(disassemble(dxc, dump / f"{s.hash}.dxil"))
        except (DumpError, subprocess.TimeoutExpired) as error:
            return s.hash, str(error)

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        results = dict(pool.map(one, wanted))
    failures = {h: r for h, r in results.items() if isinstance(r, str)}
    for h, r in results.items():
        if isinstance(r, dict):
            shaders[h].reads = r
    out: dict = {"disassembled": len(results) - len(failures), "failures": failures}
    handle_ops = collections.Counter(op for r in results.values() if isinstance(r, dict)
                                     for op in r["handle_ops"])
    out["handle_ops"] = dict(handle_ops)
    unresolved = sum(sum(r["unresolved_loads"].values()) for r in results.values()
                     if isinstance(r, dict))
    out["unresolved_cbuffer_loads"] = unresolved

    for kind in ("vs", "hs", "ds", "gs"):
        group = [s for s in shaders.values() if s.kind == kind and s.reads]
        resources = collections.Counter()
        members = collections.Counter()
        for s in group:
            for b in s.reads["bindings"]:
                resources[f"{b['name']} ({b['kind']} {b['bind']})"] += 1
            for buffer, read in s.reads["reads"].items():
                for m in read:
                    members[f"{buffer}.{m}"] += 1
        out[kind] = {"shaders": len(group), "resources": dict(resources.most_common()),
                     "members_read": dict(members.most_common())}

    declared = collections.Counter()
    read_wind = collections.Counter()
    resources_wind = collections.Counter()
    for s in shaders.values():
        if not s.reads:
            continue
        for buffer, names in s.reads["members"].items():
            for n in names:
                if WIND_WORDS.search(n) or WIND_WORDS.search(buffer):
                    declared[f"{buffer}.{n}"] += 1
        for buffer, names in s.reads["reads"].items():
            for n in names:
                if WIND_WORDS.search(n) or WIND_WORDS.search(buffer):
                    read_wind[f"{s.kind} {buffer}.{n}"] += 1
        for b in s.reads["bindings"]:
            if WIND_WORDS.search(b["name"]):
                resources_wind[f"{s.kind} {b['name']} {b['bind']}"] += 1
    out["wind_like_declared"] = dict(declared.most_common())
    out["wind_like_read"] = dict(read_wind.most_common())
    out["wind_like_resources"] = dict(resources_wind.most_common())

    # Who reads the previous frame's transforms, by pass.
    prev = collections.Counter()
    for s in shaders.values():
        if not s.reads:
            continue
        hits = sorted({f"{b}.{m}" for b, ms in s.reads["reads"].items() for m in ms
                       if re.search(r"prev|old", m, re.I)})
        prev[(s.kind, pass_of(s.name)[0] or family_of(s.name), " ".join(hits) or "-")] += 1
    out["previous_frame_reads"] = [
        {"kind": k, "pass": p, "reads": r, "shaders": n} for (k, p, r), n in prev.most_common(40)]

    # What each vertex-shader output is computed from, per pass: for every output element, how
    # many shaders of that pass make it from each cbuffer member, input or resource.
    made = collections.defaultdict(collections.Counter)
    totals = collections.Counter()
    for s in shaders.values():
        if s.kind != "vs" or not s.reads:
            continue
        pass_ = pass_of(s.name)[0] or family_of(s.name)
        for sig, sources in s.reads["outputs"].items():
            element = element_of(s.osg_by_id, int(sig))
            if element is None:
                continue
            key = f"{pass_} {element.name}{element.index}"
            totals[key] += 1
            for source in sources:
                made[key][input_label(s, source)] += 1
    interesting = re.compile(r"MatrixViewProj|PrevMatrixViewProj|CameraPos|ObjMatrix|World|"
                             r"in:POSITION|in:SV_|SpeedTree|force|Cloth|Time|VAT|res:")
    out["vs_outputs_made_of"] = {
        key: {"shaders": totals[key],
              "sources": {k: v for k, v in made[key].most_common() if interesting.search(k)}}
        for key, _ in totals.most_common(80)}

    # Outputs that are the clip-space position again: every component stored from the same value
    # as `SV_Position`'s. And outputs built from the previous frame's view-projection.
    copies = collections.Counter()
    previous = collections.Counter()
    for s in shaders.values():
        if s.kind != "vs" or not s.reads:
            continue
        pass_ = pass_of(s.name)[0] or family_of(s.name)
        position = next((n for n, e in enumerate(s.osg_by_id) if e and e.system_value == 1),
                        None)
        stored = {int(k): v for k, v in s.reads["stored"].items()}
        for sig, element in enumerate(s.osg_by_id):
            if element is None:
                continue
            label = f"{pass_} {element.name}{element.index}@{element.register}"
            if position is not None and sig != position and stored.get(sig) and \
                    stored.get(sig) == stored.get(position):
                copies[label] += 1
            sources = s.reads["outputs"].get(sig) or s.reads["outputs"].get(str(sig)) or []
            if any("PrevMatrixViewProj" in x for x in sources):
                previous[label] += 1
    out["vs_outputs_copying_sv_position"] = dict(copies.most_common())
    out["vs_outputs_from_previous_view_projection"] = dict(previous.most_common())

    # The screen-space passes that make or use motion vectors: what they bind and read.
    motion = {}
    for s in shaders.values():
        if s.kind == "ps" and s.reads and re.search(
                r"Velocity|MotionVector|MotionBlur|TempAA|TAA", s.name):
            motion[s.short] = {
                "bindings": [f"{b['name']} {b['bind']}" for b in s.reads["bindings"]],
                "reads": {k: v for k, v in s.reads["reads"].items() if k != "cbSceneParam"}}
    out["motion_vector_passes"] = motion
    velo_ps = collections.Counter()
    for s in shaders.values():
        if s.kind == "ps" and s.reads and pass_of(s.name)[0] == "Velo":
            sources = set()
            for sig, made_from in s.reads["outputs"].items():
                for x in made_from:
                    sources.add(input_label(s, x))
            velo_ps[" ".join(sorted(x for x in sources if x.startswith("in:")
                                    or "Matrix" in x or "Velocity" in x))] += 1
    out["velo_ps_target_made_of"] = dict(velo_ps.most_common(12))
    out["vs_by_pass"] = vs_by_pass(shaders)
    out["vs_world_position_outputs"] = world_position_outputs(shaders)
    return out


def world_position_outputs(shaders: dict[str, Shader]) -> dict:
    """Vertex-shader outputs other than `SV_Position` built from the input position and an object
    transform but from no view-projection: candidates for a world-space position, split by whether
    the camera position is subtracted (camera-relative) or not."""
    found = {"camera_relative": collections.Counter(), "world": collections.Counter()}
    examples: dict[str, str] = {}
    for s in shaders.values():
        if s.kind != "vs" or not s.reads:
            continue
        pass_ = pass_of(s.name)[0] or family_of(s.name)
        for sig, sources in s.reads["outputs"].items():
            element = element_of(s.osg_by_id, int(sig))
            if element is None or element.system_value == 1:
                continue
            labels = {input_label(s, x) for x in sources}
            transformed = any(re.search(r"ObjMatrix|mWorld|VC_World\b", x) for x in labels)
            if "in:POSITION0" not in labels or not transformed or \
                    any("MatrixViewProj" in x for x in labels):
                continue
            key = f"{pass_} {element.name}{element.index}@{element.register}"
            bucket = "camera_relative" if any("CameraPos" in x for x in labels) else "world"
            found[bucket][key] += 1
            examples.setdefault(key, s.short)
    return {"camera_relative": dict(found["camera_relative"].most_common()),
            "world": dict(found["world"].most_common()),
            "examples": examples}


def vs_by_pass(shaders: dict[str, Shader]) -> dict:
    """Per pass, what its vertex shaders are made of: how many, which material families, how many
    skin, whether `POSITION1` is a copy of the clip position, whether any output is built from the
    previous frame's view-projection, what `SV_Position` is computed from, and the constant
    buffers bound."""
    matrixish = re.compile(r"Matrix|CameraPos|ObjMatrix|World|SpeedTree|force|Time|VAT|res:|"
                           r"in:POSITION|in:BLEND|in:SV_")
    passes: dict[str, dict] = {}
    for s in shaders.values():
        if s.kind != "vs" or not s.reads:
            continue
        pass_, modifiers = pass_of(s.name)
        row = passes.setdefault(pass_ or family_of(s.name), {
            "shaders": 0, "skin": 0, "families": collections.Counter(),
            "instance_id": 0, "position1": 0, "position1_is_clip_copy": 0,
            "prev_clip_output": 0, "global_time": 0,
            "sv_position_from": collections.Counter(), "cbuffers": collections.Counter()})
        row["shaders"] += 1
        row["skin"] += "Skin" in modifiers
        row["families"][family_of(s.name)] += 1
        row["instance_id"] += any(e.system_value == 8 for e in s.isg)
        stored = {int(k): v for k, v in s.reads["stored"].items()}
        outputs = {int(k): v for k, v in s.reads["outputs"].items()}
        position = next((n for n, e in enumerate(s.osg_by_id) if e and e.system_value == 1),
                        None)
        for sig, element in enumerate(s.osg_by_id):
            if element is None:
                continue
            if element.name.upper() == "POSITION" and element.index == 1:
                row["position1"] += 1
                row["position1_is_clip_copy"] += position is not None and \
                    stored.get(sig) is not None and stored.get(sig) == stored.get(position)
        row["prev_clip_output"] += any("PrevMatrixViewProj" in x
                                       for sources in outputs.values() for x in sources)
        row["global_time"] += any("FC_GlobalTime" in m for ms in s.reads["reads"].values()
                                  for m in ms)
        if position is not None:
            for source in outputs.get(position, ()):
                label = input_label(s, source)
                if matrixish.search(label):
                    row["sv_position_from"][label] += 1
        for binding in s.reads["bindings"]:
            if binding["kind"] == "cbuffer":
                row["cbuffers"][f"{binding['name']} {binding['bind']}"] += 1
    for row in passes.values():
        for key in ("families", "sv_position_from", "cbuffers"):
            row[key] = dict(row[key].most_common())
    return dict(sorted(passes.items(), key=lambda item: -item[1]["shaders"]))


# ---- output -------------------------------------------------------------------------------------

def trim(value, most: int):
    """`value` with every dict and list cut to its first `most` entries, saying how many went."""
    if isinstance(value, dict):
        items = list(value.items())
        out = {k: trim(v, most) for k, v in items[:most]}
        if len(items) > most:
            out["..."] = f"{len(items) - most} more"
        return out
    if isinstance(value, list) and not (value and isinstance(value[0], (int, str))
                                        and len(value) <= 4):
        out = [trim(v, most) for v in value[:most]]
        if len(value) > most:
            out.append(f"... {len(value) - most} more")
        return out
    return value


def print_report(report: dict, sections: set[str], most: int = 30) -> None:
    def show(title: str, value) -> None:
        print(f"\n== {title}")
        print(json.dumps(trim(value, most), indent=1, default=str))

    print(f"dump {report['dump']}")
    print(f"files {report['files']}  parse errors {len(report['parse_errors'])}")
    print(f"file name == fnv1_64(bytes): {report['file_name_is_fnv1_64_of_bytes']}")
    for name in ("stages", "rootsig", "vs", "hs_ds_gs", "pipelines", "passes", "reads"):
        if name in report and (name in sections or (name == "hs_ds_gs" and "pipelines" in sections)):
            show(name, report[name])


def selftest() -> int:
    failures = []

    def expect(what: str, got, want) -> None:
        if got != want:
            failures.append(f"{what}: got {got!r}, want {want!r}")

    def sig(elements: list[tuple[str, int, int, int, int, int]]) -> bytes:
        names = b""
        name_at = {}
        for e in elements:
            if e[0] not in name_at:
                name_at[e[0]] = 8 + 32 * len(elements) + len(names)
                names += e[0].encode() + b"\0"
        table = b"".join(struct.pack("<6IBBHI", 0, name_at[e[0]], e[1], e[2], 3, e[3], e[4], 0,
                                     0, 0) for e in elements)
        _ = [e[5] for e in elements]
        return struct.pack("<II", len(elements), 8) + table + names

    def container(parts: list[tuple[bytes, bytes]]) -> bytes:
        header = 32 + 4 * len(parts)
        body, offsets = b"", []
        for code, data in parts:
            offsets.append(header + len(body))
            body += code + struct.pack("<I", len(data)) + data
        blob = b"DXBC" + b"\0" * 16 + struct.pack("<HHII", 1, 0, header + len(body), len(parts))
        return blob + b"".join(struct.pack("<I", o) for o in offsets) + body

    # OSG1 lists by register, so the packed `TEXCOORD9@2.yzw` comes before `TEXCOORD7@3`, while
    # its DXIL element ID (the order PSV0 lists elements in) comes after.
    osg = sig([("SV_Position", 0, 1, 0, 0xF, 0), ("POSITION", 1, 0, 1, 0xF, 0),
               ("TEXCOORD", 4, 0, 2, 0x1, 0), ("TEXCOORD", 9, 0, 2, 0xE, 0),
               ("TEXCOORD", 7, 0, 3, 0xF, 0)])
    isg = sig([("POSITION", 0, 0, 0, 0x7, 0), ("SV_VertexID", 0, 6, 1, 0x1, 0)])
    runtime = bytes([1]) + b"\0" * 23 + bytes([1, 0, 0, 0, 2, 5, 0, 0, 0, 0, 0, 0])
    binds = struct.pack("<4I", 2, 0, 4, 4)
    strings = b"\0POSITION\0TEXCOORD\0\0"
    semantic_indexes = [0, 1, 4, 7, 9]

    def placed(name_at: int, index_at: int, row: int, col: int, cols: int) -> bytes:
        return struct.pack("<IIBBBBBBBB", name_at, index_at, 1, row, cols | col << 4 | 0x40,
                           0, 3, 0, 0, 0)
    elements = b"".join([
        placed(1, 0, 0, 0, 3), placed(0, 0, 1, 0, 1),                      # inputs
        placed(0, 0, 0, 0, 4), placed(1, 1, 1, 0, 4), placed(10, 2, 2, 0, 1),
        placed(10, 3, 3, 0, 4), placed(10, 4, 2, 1, 3)])                    # outputs, by ID
    psv = struct.pack("<I", len(runtime)) + runtime + struct.pack("<II", 1, 16) + binds + \
        struct.pack("<I", len(strings)) + strings + \
        struct.pack(f"<I{len(semantic_indexes)}I", len(semantic_indexes), *semantic_indexes) + \
        struct.pack("<I", 16) + elements
    program = struct.pack("<II", (1 << 16) | 0x60, 6) + b"DXIL" + struct.pack("<III", 0x100, 16, 0)
    name = "N:\\x\\M[AMSN]_Gbuf_Skin@[cl].vpo.pdb".encode()
    ildn = struct.pack("<HH", 0, len(name)) + name + b"\0\0\0\0"
    rts0 = struct.pack("<6I", 2, 1, 24, 0, 60, 0x1) + struct.pack("<3I", 2, 1, 36) + \
        struct.pack("<3I", 3, 7, 0)
    blob = container([(b"ISG1", isg), (b"OSG1", osg), (b"PSV0", psv), (b"DXIL", program),
                      (b"ILDN", ildn), (b"RTS0", rts0)])
    parts = parts_of(blob)
    expect("kind", program_kind(parts), ("vs", 6, 0))
    out = signature(parts[b"OSG1"])
    expect("osg labels", [e.label() for e in out],
           ["SV_Position0@0.xyzw/POS", "POSITION1@1.xyzw", "TEXCOORD4@2.x", "TEXCOORD9@2.yzw",
            "TEXCOORD7@3.xyzw"])
    expect("isg system values", [e.system_value for e in signature(parts[b"ISG1"])], [0, 6])
    p = psv0(parts[b"PSV0"])
    expect("psv stage", p.stage, 1)
    expect("psv binds", p.binds, [("cbv", 0, 4, 4)])
    expect("psv output ids", [(e.name, e.index, e.start_row, e.start_col)
                              for e in p.elements["out"]],
           [("", 0, 0, 0), ("POSITION", 1, 1, 0), ("TEXCOORD", 4, 2, 0), ("TEXCOORD", 7, 3, 0),
            ("TEXCOORD", 9, 2, 1)])
    expect("outputs by element id", [e.label() if e else None
                                     for e in by_element_id(out, p.elements["out"])],
           ["SV_Position0@0.xyzw/POS", "POSITION1@1.xyzw", "TEXCOORD4@2.x", "TEXCOORD7@3.xyzw",
            "TEXCOORD9@2.yzw"])
    expect("inputs by element id", [e.label() if e else None for e in by_element_id(
        signature(parts[b"ISG1"]), p.elements["in"])],
        ["POSITION0@0.xyz", "SV_VertexID0@1.x/VERTID"])
    expect("ildn", debug_name(parts), "N:\\x\\M[AMSN]_Gbuf_Skin@[cl].vpo.pdb")
    expect("pass", pass_of(debug_name(parts)), ("Gbuf", ("Skin",)))
    expect("pass of a forward tessellated name", pass_of("M[Water][ocean7]_Fwd_FT.dpo.pdb"),
           ("Fwd", ("FT",)))
    expect("pass of an alpha variant", pass_of("M[A]_Gbuf_[A].ppo.pdb"), ("Gbuf", ()))
    expect("no pass in a filter", pass_of("ToneMapVS.vpo.pdb"), ("", ()))
    rs = root_signature(parts[b"RTS0"])
    expect("rs", (rs.version, rs.flag_names(), rs.params),
           (2, ["ALLOW_INPUT_ASSEMBLER_INPUT_LAYOUT"],
            [{"type": "cbv", "visibility": "vs", "register": "b3 space7"}]))
    expect("rs dwords", rs.dwords(), 2)
    try:
        parts_of(blob[:-1])
        failures.append("a truncated container was accepted")
    except DumpError:
        pass

    # The cache layout: a header, then one entry with a vertex and a pixel shader chunk.
    def chunk(ctype: int, stage: int, payload: bytes) -> bytes:
        return struct.pack("<HHI", ctype, stage, len(payload)) + payload + \
            b"\0" * (-len(payload) % 8)
    entry = b"VKB\x04" + b"\0" * 44 + chunk(5, 0, b"\0" * 56) + \
        chunk(4, 1, struct.pack("<Q", 0x1234) + b"\0" * 24) + \
        chunk(4, 0x10, struct.pack("<Q", 0xABCD) + b"\0" * 24)
    cache = b"VKS\x04" + b"\0" * 44 + struct.pack("<QQII", 1, 2, len(entry), 2) + entry
    pipelines = read_cache(cache)
    expect("cache stages", [p.stages for p in pipelines],
           [{"vs": "0000000000001234", "ps": "000000000000abcd"}])
    try:
        read_cache(cache + b"\0")
        failures.append("trailing bytes after the last cache entry were accepted")
    except DumpError:
        pass

    # Disassembly reading: a strided member read through `x * 13 + 4` and a constant one.
    text = """;
; Buffer Definitions:
;
; cbuffer cbInstanceData
; {
;
;   struct hostlayout.cbInstanceData
;   {
;
;       struct hostlayout.struct.PerInstanceData
;       {
;
;           column_major float4x3 mWorld;             ; Offset:    0
;           float4 userClipPlane;                     ; Offset:   48
;           uint matricesData;                        ; Offset:   64
;           uint cmMatricesOffset;                    ; Offset:   68
;           float4 aColor[4];                         ; Offset:  144
;
;       } VC_InstanceData[2];;                        ; Offset:    0
;
;
;   } cbInstanceData;                                 ; Offset:    0 Size:   416
;
; }
;
; cbuffer cbSceneParam
; {
;
;   struct hostlayout.cbSceneParam
;   {
;
;       float4 FC_FarClipInfo;                        ; Offset:    0
;       column_major float4x4 VC_PrevMatrixViewProj;  ; Offset:   16
;       float4 SC_CameraPos;                          ; Offset:   80
;
;   } cbSceneParam;                                   ; Offset:    0 Size:    96
;
; }
;
;
; Resource Bindings:
;
; Name                                 Type  Format         Dim      ID      HLSL Bind  Count
; ------------------------------ ---------- ------- ----------- ------- -------------- ------
; cbSceneParam                      cbuffer      NA          NA     CB0            cb8     1
; cbInstanceData                    cbuffer      NA          NA     CB1            cb4     1
;
;
define void @main() {
  %1 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 2, i32 1, i32 4, i1 false)
  %2 = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 2, i32 0, i32 8, i1 false)
  %3 = mul i32 %9, 13
  %4 = add i32 %3, 4
  %5 = call %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32 59, %dx.types.Handle %1, i32 %4)
  %6 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %2, i32 2)
  %7 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %2, i32 %8)
  %10 = extractvalue %dx.types.CBufRet.i32 %5, 0
  %11 = extractvalue %dx.types.CBufRet.f32 %6, 1
  %12 = fmul fast float %11, %11
  call void @dx.op.storeOutput.f32(i32 5, i32 3, i32 0, i8 0, float %12)
  call void @dx.op.storeOutput.f32(i32 5, i32 1, i32 0, i8 0, float 0.000000e+00)
}
"""
    found = analyse_disassembly(text)
    expect("bindings", [b["name"] for b in found["bindings"]], ["cbSceneParam", "cbInstanceData"])
    expect("strided read", found["reads"]["cbInstanceData"], ["VC_InstanceData[].matricesData"])
    expect("constant read and a dynamic one", found["reads"]["cbSceneParam"],
           ["<dynamic>", "VC_PrevMatrixViewProj"])
    expect("unresolved", found["unresolved_loads"], {"cbSceneParam": 1})
    expect("an output traced to the member it is made from", found["outputs"],
           {3: ["cbSceneParam.VC_PrevMatrixViewProj"]})
    expect("members", found["members"]["cbInstanceData"],
           ["VC_InstanceData[].aColor[]", "VC_InstanceData[].cmMatricesOffset",
            "VC_InstanceData[].mWorld", "VC_InstanceData[].matricesData",
            "VC_InstanceData[].userClipPlane"])
    expect("fnv1_64 of empty input", fnv1_64(b""), 0xCBF29CE484222325)
    expect("pipeline rows",
           pipeline_rows([Pipeline({"vs": "aa", "ps": "bb"}, "rr"), Pipeline({"cs": "cc"})]),
           ["rr\taa\t-\t-\t-\tbb", "-\t-\t-\t-\t-\t-"])
    for failure in failures:
        print(f"selftest: {failure}", file=sys.stderr)
    print(f"selftest: {'FAIL' if failures else 'ok'} ({len(failures)} failures)")
    return 1 if failures else 0


def main() -> int:
    sections_all = ["stages", "rootsig", "vs", "pipelines", "passes", "reads"]
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dump", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--dxc", type=Path, default=DXC_DEFAULT)
    parser.add_argument("--no-disasm", action="store_true", help="skip the dxc -dumpbin pass")
    parser.add_argument("--json", type=Path, help="write the whole report here")
    parser.add_argument("--section", action="append", choices=sections_all)
    parser.add_argument("--most", type=int, default=30,
                        help="entries printed per table; --json always holds them all")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--pipelines-tsv", type=Path,
                        help="write one row per cached pipeline (root signature, vs, hs, ds, gs, "
                             "ps) here and stop; the vertex splice sweep reads it")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    dump = args.dump or default_dump()
    if not dump.is_dir():
        print(f"no dump at {dump}; set ER_VKD3D_DUMP_ROOT or pass --dump", file=sys.stderr)
        return 2
    cache = args.cache
    if cache is None:
        found = sorted((dump.parent / "cache").glob("*.cache.write"))
        cache = found[0] if found else None
    if args.pipelines_tsv:
        if cache is None:
            print(f"no pipeline cache beside {dump}; pass --cache", file=sys.stderr)
            return 2
        rows = pipeline_rows(read_cache(cache.read_bytes()))
        args.pipelines_tsv.write_text("".join(row + "\n" for row in rows))
        print(f"wrote {len(rows)} pipelines to {args.pipelines_tsv.resolve()}")
        return 0
    sections = set(args.section or sections_all)
    dxc = None if args.no_disasm or not args.dxc.exists() else args.dxc
    report = census(dump, cache, dxc, sections)
    report["cache"] = str(cache) if cache else None
    if args.json:
        args.json.write_text(json.dumps(report, indent=1, default=str))
        print(f"wrote {args.json.resolve()}")
    print_report(report, sections, args.most)
    return 0


if __name__ == "__main__":
    sys.exit(main())
