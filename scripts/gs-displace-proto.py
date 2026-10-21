#!/usr/bin/env python3
"""Prototype: a pass-through geometry shader generated from a game vertex shader's output signature.

Evaluation only (shader editor, vertex-displacement question). Nothing here is wired into a gate.

Subcommands, in the order they are run:
    `census out.json`   read every `.dxil` in the vkd3d dump: kind, debug name, ISG1/OSG1/PSG1,
                        PSV0 interpolation and binds, SFI0, RTS0 flags and parameter visibility.
    `dataflow census.json out.json`
                        for each vertex and domain shader, which SSA values each output
                        component stores and which `cbSceneParam` rows (view-projection,
                        previous view-projection, camera position, inverse view-projection) they
                        read, mapped to `OSG1` elements through the program's metadata.
    `generate census.json work/ [--dataflow dataflow.json]`
                        for each distinct output signature (and move plan) of a vertex or domain
                        shader that feeds the rasterizer, write a pass-through geometry shader
                        that moves the clip position, its copies and the previous-frame clip
                        position through a `displace()` layer, compile it with dxc (gs_6_0),
                        validate it with dxv, and check its ISG1 and OSG1 against that signature.

The dump is `$ER_VKD3D_DUMP_ROOT`, else `.worktrees/er-shaders/target/vkd3d-dump/run1/spv` found the
way `build-cel-shaders.py` finds it. It is read only.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("build_cel_shaders", HERE / "build-cel-shaders.py")
bcs = importlib.util.module_from_spec(_spec)
sys.modules["build_cel_shaders"] = bcs
_spec.loader.exec_module(bcs)

KINDS = {0: "ps", 1: "vs", 2: "gs", 3: "hs", 4: "ds", 5: "cs", 6: "lib"}
# A cap on each dxc or dxv run, as `scripts/check-no-timeouts.py` requires.
TIMEOUT_SECONDS = 30


def ildn(container) -> str | None:
    part = container.part(b"ILDN")
    if part is None:
        return None
    length = struct.unpack_from("<H", part, 2)[0]
    return part[4:4 + length].decode("utf-8", "replace")


def rts0(part: bytes | None) -> dict | None:
    """The serialized root signature's header and each parameter's type and visibility."""
    if part is None:
        return None
    version, count, at, samplers, _, flags = struct.unpack_from("<6I", part, 0)
    params = [struct.unpack_from("<2I", part, at + 12 * index) for index in range(count)]
    return {"version": version, "flags": flags, "params": params, "samplers": samplers}


def element_tuple(element) -> tuple:
    return (element.name, element.semantic_index, element.system_value, element.component_type,
            element.register, element.mask, element.rw_mask, element.min_precision, element.stream)


def read_shader(path: Path) -> dict:
    data = path.read_bytes()
    container = bcs.parse_container(data)
    kind, major, minor = bcs.program_model(container)
    row = {"hash": path.stem, "kind": KINDS.get(kind, str(kind)), "model": f"{major}.{minor}",
           "name": ildn(container), "bytes": len(data)}
    for code in (b"ISG1", b"OSG1", b"PSG1"):
        part = container.part(code)
        if part is not None:
            row[code.decode()] = [element_tuple(e) for e in bcs.parse_signature(part, code.decode())]
    psv = container.part(b"PSV0")
    if psv is not None:
        parsed = bcs.parse_psv0(psv)
        row["interp"] = parsed.interpolation
        row["binds"] = parsed.binds
        row["psv_runtime"] = psv[4:4 + struct.unpack_from("<I", psv, 0)[0]].hex()
    row["sfi0"] = bcs.sfi0(container) if container.part(b"SFI0") is not None else None
    row["rts0"] = rts0(container.part(b"RTS0"))
    return row


def census(out: Path) -> int:
    root = bcs.dump_root()
    if root is None:
        print("gs-displace-proto: no vkd3d dump found", file=sys.stderr)
        return 2
    rows, unreadable = [], 0
    for path in sorted(root.glob("*.dxil")):
        try:
            rows.append(read_shader(path))
        except bcs.Refused as error:
            unreadable += 1
            print(f"unreadable {path.name}: {error}", file=sys.stderr)
    out.write_text(json.dumps(rows))
    print(f"read {len(rows)} from {root}, unreadable {unreadable}")
    print(collections.Counter(row["kind"] for row in rows))
    return 0


# ---------------------------------------------------------------------------------------------
# The generator: one pass-through geometry shader per distinct output signature.
# ---------------------------------------------------------------------------------------------

# `DXIL::InterpolationMode` to the HLSL modifiers that produce it on an output.
INTERP_MODIFIERS = {
    0: "", 1: "nointerpolation ", 2: "", 3: "centroid ", 4: "noperspective ",
    5: "noperspective centroid ", 6: "sample ", 7: "noperspective sample ",
}
# `DxilProgramSigCompType` to an HLSL scalar.
COMPONENT_TYPES = {1: "uint", 2: "int", 3: "float"}
SYSTEM_VALUE_POSITION = 1
SYSTEM_VALUE_CLIP = 2
SYSTEM_VALUE_CULL = 3

# What a layer author writes. `world_pos` is the vertex's world position, rebuilt from its clip
# position through the view's own inverse view-projection; the result is a world-space offset.
LAYER = """\
float3 displace(float3 world_pos, VertexCtx ctx)
{
    float phase = dot(world_pos.xz, float2(0.7, 1.3)) + ctx.time * 3.0;
    return float3(0.0, 0.05 * sin(phase), 0.0);
}
"""

# The prelude every generated shader shares. `b0, space42` is the editor's parameter block (design
# 5.2); `b8, space43` is the game's `cbSceneParam` seen through a geometry-visible copy of the
# vertex stage's `CBV b4..b13` table (see the report). Offsets are the game's reflection data.
PRELUDE = """\
cbuffer ErParams : register(b0, space42) { float4 er_p[1024]; };
cbuffer cbSceneParam : register(b8, space43)
{
    float4 SC_CameraPos : packoffset(c3);
    float4x4 FC_MatrixInvViewProj : packoffset(c8);
    float4x4 VC_MatrixViewProj : packoffset(c24);
    float4x4 VC_PrevMatrixViewProj : packoffset(c29);
};
struct VertexCtx { float time; float3 world_pos; float4 clip; uint corner; };
"""

# `er_p[3].x` is the time in seconds (design 5.2); `er_p[32].x`, in the reserved range, is the
# previous frame's time, an addition this prototype assumes.
BODY = """\
float3 er_world(float4 clip)
{
    float4 rel = mul(clip, FC_MatrixInvViewProj);
    return rel.xyz / rel.w + SC_CameraPos.xyz;
}

float3 er_offset(float3 world_pos, float4 clip, uint corner, float time)
{
    VertexCtx ctx;
    ctx.time = time;
    ctx.world_pos = world_pos;
    ctx.clip = clip;
    ctx.corner = corner;
    return displace(world_pos, ctx);
}

[maxvertexcount(3)]
void gs_main(triangle ErVertex v[3], inout TriangleStream<ErVertex> stream)
{
    [unroll] for (uint corner = 0; corner < 3; ++corner) {
        ErVertex o = v[corner];
%(moves)s        stream.Append(o);
    }
}
"""

SWIZZLE = "xyzw"


def popcount(mask: int) -> int:
    return bin(mask).count("1")


def link_key(row: dict) -> tuple:
    """What linking compares, element by element, plus the output interpolation modes."""
    elements = tuple(tuple(e[:6]) + (e[7], e[8]) for e in row["OSG1"])
    modes = tuple(row["interp"][1]) if row.get("interp") else None
    return elements, modes


def plan_for(row: dict, flow: dict | None) -> dict:
    """Which outputs the geometry shader moves, from the stage's dataflow.

    `copies` are (element, component, clip component) where the stage stores the very value it
    stores in that `SV_Position` component; `prev` are whole `float4` outputs made with the previous
    view-projection; `stale` names outputs made from the view-projection some other way, which the
    shader cannot redo and leaves as they are. Without dataflow only `SV_Position` moves.
    """
    sig = row["OSG1"]
    position = next(i for i, e in enumerate(sig) if e[2] == SYSTEM_VALUE_POSITION)
    plan = {"position": position, "copies": [], "prev": [], "stale": [], "scene": None}
    if flow is not None and "error" in flow:
        plan["stale"].append(f"dataflow unreadable: {flow['error']}")
    if flow is None or str(position) not in flow:
        return plan
    clip_values = {value: int(column) for column, value in flow[str(position)]["values"].items()
                   if value.startswith("%")}
    plan["scene"] = flow[str(position)]["reads"] == ["camera_pos", "view_proj"]
    if len(clip_values) != 4:
        plan["stale"].append("SV_Position is not four distinct values")
    for index, element in enumerate(sig):
        if index == position or str(index) not in flow:
            continue
        stored = flow[str(index)]
        copied = {int(column): clip_values[value] for column, value in stored["values"].items()
                  if value in clip_values}
        for column, clip_column in sorted(copied.items()):
            plan["copies"].append((index, column, clip_column))
        label = f"{element[0]}{element[1]}"
        if copied:
            if len(copied) != len(stored["values"]):
                plan["stale"].append(f"{label} is partly a clip copy")
        elif "prev_view_proj" in stored["reads"]:
            if element[5] == 0xF and element[3] == 3:
                plan["prev"].append(index)
            else:
                plan["stale"].append(f"{label} is made from the previous view-projection")
        elif "view_proj" in stored["reads"]:
            plan["stale"].append(f"{label} is made from the view-projection")
    return plan


def generate_hlsl(row: dict, plan: dict) -> str:
    """The geometry shader for `row`'s output signature, moving what `plan` says."""
    modes = row["interp"][1]
    fields = []
    for index, (element, mode) in enumerate(zip(row["OSG1"], modes)):
        name, semantic_index, system_value, component_type, _, mask, *_ = element
        scalar = COMPONENT_TYPES.get(component_type)
        if scalar is None:
            raise ValueError(f"component type {component_type} of {name}{semantic_index}")
        count = popcount(mask)
        hlsl_type = scalar if count == 1 else f"{scalar}{count}"
        modifier = "" if system_value == SYSTEM_VALUE_POSITION else INTERP_MODIFIERS[mode]
        fields.append(f"    {modifier}{hlsl_type} f{index} : {name}{semantic_index};")
    p = f"o.f{plan['position']}"
    moves = [
        f"        float3 world = er_world({p});",
        f"        precise float4 moved = {p} + mul(float4(er_offset(world, {p}, corner, "
        f"er_p[3].x), 0.0), VC_MatrixViewProj);",
        f"        {p} = moved;",
    ]
    for index, column, clip_column in plan["copies"]:
        moves.append(f"        o.f{index}.{SWIZZLE[column]} = moved.{SWIZZLE[clip_column]};")
    for index in plan["prev"]:
        moves.append(f"        o.f{index} = o.f{index} + mul(float4(er_offset(world, o.f{index}, "
                     f"corner, er_p[32].x), 0.0), VC_PrevMatrixViewProj);")
    return (PRELUDE + "struct ErVertex\n{\n" + "\n".join(fields) + "\n};\n" + LAYER
            + BODY % {"moves": "".join(line + "\n" for line in moves)})


def links_equal(ours: list, theirs: list) -> bool:
    return len(ours) == len(theirs) and all(a.links_like(b) for a, b in zip(ours, theirs))


def check_gs(blob: bytes, row: dict) -> list[str]:
    """Why the compiled geometry shader would not sit between `row` and the game's next stage."""
    problems = []
    container = bcs.parse_container(blob)
    if not bcs.is_signed_correctly(container):
        problems.append("not signed with the retail hash")
    if bcs.program_model(container) != (2, 6, 0):
        problems.append(f"not gs_6_0: {bcs.program_model(container)}")
    theirs = [bcs.SigElement(name=e[0], semantic_index=e[1], system_value=e[2],
                             component_type=e[3], register=e[4], mask=e[5], rw_mask=e[6],
                             min_precision=e[7], stream=e[8]) for e in row["OSG1"]]
    isg = bcs.parse_signature(container.required(b"ISG1"), "ISG1")
    osg = bcs.parse_signature(container.required(b"OSG1"), "OSG1")
    if not links_equal(isg, theirs):
        problems.append("ISG1 differs from the stage's OSG1")
    if not links_equal(osg, theirs):
        problems.append("OSG1 differs from the stage's OSG1")
    psv = bcs.parse_psv0(container.required(b"PSV0"))
    if psv.interpolation is None or tuple(psv.interpolation[1]) != tuple(row["interp"][1]):
        problems.append(f"output interpolation {psv.interpolation and psv.interpolation[1]} "
                        f"vs {row['interp'][1]}")
    return problems


def compile_one(dxc: Path, dxv: Path, source: str, work: Path, stem: str) -> tuple[bytes | None, str]:
    hlsl = work / f"{stem}.hlsl"
    out = work / f"{stem}.dxil"
    hlsl.write_text(source)
    argv = [str(dxc), "-T", "gs_6_0", "-E", "gs_main", "-O3", "-Qstrip_debug",
            "-HV", bcs.HLSL_VERSION, "-validator-version", bcs.VALIDATOR_VERSION,
            str(hlsl), "-Fo", str(out)]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    check = subprocess.run([str(dxv), str(out)], capture_output=True, text=True,
                           timeout=TIMEOUT_SECONDS)
    if check.returncode != 0:
        return None, "dxv: " + (check.stdout + check.stderr).strip()
    return out.read_bytes(), (check.stdout + check.stderr).strip()


def plan_key(plan: dict) -> tuple:
    return (plan["position"], tuple(plan["copies"]), tuple(plan["prev"]))


def generate(census_path: Path, work: Path, flow_path: Path | None) -> int:
    dxc = bcs.find_dxc()
    if dxc is None:
        print("gs-displace-proto: no dxc", file=sys.stderr)
        return 2
    dxv = dxc.with_name("dxv")
    work.mkdir(parents=True, exist_ok=True)
    rows = json.loads(census_path.read_text())
    flows = json.loads(flow_path.read_text()) if flow_path else {}
    feeding = [row for row in rows if row["kind"] in ("vs", "ds") and row.get("OSG1")
               and any(e[2] == SYSTEM_VALUE_POSITION for e in row["OSG1"])]
    shapes: dict[tuple, list[tuple[dict, dict]]] = collections.defaultdict(list)
    for row in feeding:
        plan = plan_for(row, flows.get(row["hash"]) if flow_path else None)
        shapes[(link_key(row), plan_key(plan))].append((row, plan))
    report = []
    started = time.monotonic()
    for number, members in enumerate(shapes.values()):
        row, plan = members[0]
        stem = f"shape{number:03d}_{row['kind']}_{row['hash']}"
        entry = {"shape": number, "rep": row["hash"], "kind": row["kind"],
                 "members": [member["hash"] for member, _ in members],
                 "moves": {"copies": len(plan["copies"]), "prev": len(plan["prev"])}}
        try:
            source = generate_hlsl(row, plan)
        except ValueError as error:
            report.append(entry | {"ok": False, "why": [f"generator: {error}"]})
            continue
        blob, message = compile_one(dxc, dxv, source, work, stem)
        problems = check_gs(blob, row) if blob is not None else [message]
        report.append(entry | {"ok": not problems, "why": problems,
                               "dxv": message if blob is not None else None,
                               "bytes": len(blob) if blob else None})
    elapsed = time.monotonic() - started
    (work / "report.json").write_text(json.dumps(report, indent=1))
    plans = {row["hash"]: plan for members in shapes.values() for row, plan in members}
    (work / "plans.json").write_text(json.dumps(plans))
    ok = [r for r in report if r["ok"]]
    covered = sum(len(r["members"]) for r in ok)
    print(f"stages feeding the rasterizer: {len(feeding)} "
          f"({sum(1 for r in feeding if r['kind'] == 'vs')} vs, "
          f"{sum(1 for r in feeding if r['kind'] == 'ds')} ds)")
    print(f"distinct (signature, move plan) shapes: {len(shapes)}; compiled, validated and "
          f"linked: {len(ok)}; stages covered {covered} of {len(feeding)}; "
          f"{elapsed:.1f} s for {len(shapes)} compiles")
    if flow_path:
        scene = sum(1 for p in plans.values() if p["scene"])
        clean = sum(1 for p in plans.values() if p["scene"] and not p["stale"])
        print(f"SV_Position made from cbSceneParam: {scene}; of those with nothing left stale: "
              f"{clean}")
    for failed in (r for r in report if not r["ok"]):
        print(f"  shape {failed['shape']} ({len(failed['members'])} stages, rep {failed['rep']}):"
              f" {failed['why']}")
    return 0 if len(ok) == len(report) else 1


# ---------------------------------------------------------------------------------------------
# Dataflow: what each vertex or domain shader output is made from, read from `dxc -dumpbin`.
# ---------------------------------------------------------------------------------------------

SSA = re.compile(r"%(\d+)")
DEFINE = re.compile(r"^\s*%(\d+) = (.*)$")
STORE = re.compile(r"@dx\.op\.storeOutput\.\w+\(i32 5, i32 (\d+), i32 (\d+), i8 (\d+), \w+ ([^)]+)\)")
HANDLE = re.compile(r"@dx\.op\.createHandle\(i32 57, i8 (\d+), i32 \d+, i32 (\d+),")
CB_LOAD = re.compile(r"@dx\.op\.cbufferLoadLegacy\.\w+\(i32 59, %dx\.types\.Handle %(\d+), i32 (\S+)\)")
# Rows of `cbSceneParam` (`b8`) by what they hold, from the game's own reflection data.
SCENE_ROWS = {"camera_pos": {3}, "inv_view_proj": set(range(8, 12)),
              "view_proj": set(range(24, 28)), "prev_camera_pos": {28},
              "prev_view_proj": set(range(29, 33))}
SCENE_REGISTER = 8
CBV_CLASS = 2


METADATA = re.compile(r"^!(\d+) = !\{(.*)\}$")
ENTRY_POINTS = re.compile(r"^!dx\.entryPoints = !\{!(\d+)\}")


def output_elements(text: str) -> dict[tuple[int, int], tuple[str, int]]:
    """(element id, row) to (semantic, semantic index) for the entry point's outputs.

    `storeOutput` names an element by its id in the program's metadata, which is declaration
    order; the container's `OSG1` lists elements by register. The two orders differ, so stores
    are mapped through the metadata rather than by position.
    """
    nodes = {}
    entry = None
    for line in text.splitlines():
        match = METADATA.match(line)
        if match:
            nodes[int(match.group(1))] = [part.strip() for part in match.group(2).split(", ")]
        match = ENTRY_POINTS.match(line)
        if match:
            entry = int(match.group(1))
    if entry is None:
        return {}
    signatures = nodes[entry][2]
    if not signatures.startswith("!"):
        return {}
    outputs = nodes[int(signatures[1:])][1]
    if not outputs.startswith("!"):
        return {}
    mapping = {}
    for reference in nodes[int(outputs[1:])]:
        fields = nodes[int(reference[1:])]
        element_id = int(fields[0].split()[1])
        name = fields[1][2:-1]
        indexes = [int(part.split()[1]) for part in nodes[int(fields[4][1:])]]
        for row, semantic_index in enumerate(indexes):
            mapping[(element_id, row)] = (name, semantic_index)
    return mapping


def output_sources(text: str, osg1: list) -> dict:
    """Per `OSG1` element: the SSA values stored in each of its components, and which
    `cbSceneParam` row groups they read."""
    defs: dict[int, str] = {}
    scene_handles: set[int] = set()
    by_semantic = {(e[0], e[1]): index for index, e in enumerate(osg1)}
    elements = output_elements(text)
    stores: dict[int, dict[int, str]] = collections.defaultdict(dict)
    for line in text.splitlines():
        match = DEFINE.match(line)
        if match:
            defs[int(match.group(1))] = match.group(2)
            handle = HANDLE.search(match.group(2))
            if handle and int(handle.group(1)) == CBV_CLASS and int(handle.group(2)) == SCENE_REGISTER:
                scene_handles.add(int(match.group(1)))
        store = STORE.search(line)
        if store:
            semantic = elements.get((int(store.group(1)), int(store.group(2))))
            index = by_semantic.get(semantic) if semantic else None
            if index is None:
                raise ValueError(f"a store to output {store.group(1)} row {store.group(2)} names "
                                 f"no OSG1 element")
            stores[index][int(store.group(3))] = store.group(4).strip()
    memo: dict[int, frozenset] = {}

    def reads(value: int) -> frozenset:
        if value in memo:
            return memo[value]
        memo[value] = frozenset()
        found = set()
        stack = [value]
        seen = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            body = defs.get(current, "")
            load = CB_LOAD.search(body)
            if load and int(load.group(1)) in scene_handles and load.group(2).isdigit():
                row = int(load.group(2))
                found.update(name for name, rows in SCENE_ROWS.items() if row in rows)
            stack.extend(int(n) for n in SSA.findall(body))
        memo[value] = frozenset(found)
        return memo[value]

    result = {}
    for row, columns in stores.items():
        groups = set()
        for value in columns.values():
            if value.startswith("%"):
                groups |= reads(int(value[1:]))
        result[row] = {"values": columns, "reads": sorted(groups)}
    return result


def dataflow(census_path: Path, out: Path) -> int:
    dxc = bcs.find_dxc()
    root = bcs.dump_root()
    if dxc is None or root is None:
        print("gs-displace-proto: needs dxc and the dump", file=sys.stderr)
        return 2
    rows = json.loads(census_path.read_text())
    results = {}
    for row in rows:
        if row["kind"] not in ("vs", "ds") or not row.get("OSG1"):
            continue
        proc = subprocess.run([str(dxc), "-dumpbin", str(root / f"{row['hash']}.dxil")],
                              capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if proc.returncode != 0:
            results[row["hash"]] = {"error": proc.stderr.strip()[:200]}
            continue
        try:
            results[row["hash"]] = output_sources(proc.stdout, row["OSG1"])
        except (ValueError, KeyError, IndexError) as error:
            results[row["hash"]] = {"error": f"metadata: {error}"}
    out.write_text(json.dumps(results))
    print(f"dataflow for {len(results)} stages written to {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    census_parser = sub.add_parser("census")
    census_parser.add_argument("out", type=Path)
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("census", type=Path)
    generate_parser.add_argument("work", type=Path)
    generate_parser.add_argument("--dataflow", type=Path,
                                 help="the `dataflow` output; without it only SV_Position moves")
    dataflow_parser = sub.add_parser("dataflow")
    dataflow_parser.add_argument("census", type=Path)
    dataflow_parser.add_argument("out", type=Path)
    args = parser.parse_args()
    if args.command == "census":
        return census(args.out)
    if args.command == "generate":
        return generate(args.census, args.work, args.dataflow)
    if args.command == "dataflow":
        return dataflow(args.census, args.out)
    return 2


if __name__ == "__main__":
    sys.exit(main())
