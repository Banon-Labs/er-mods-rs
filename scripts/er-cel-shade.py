#!/usr/bin/env python3
"""Build vkd3d-proton shader overrides that cel-shade Elden Ring's sun.

Elden Ring is deferred. Every directional light is accumulated by one family of full-screen pixel
shaders in `gxrenderershader.shaderbnd.dcx`: `GXLightAcc_DDirLight1`, `GXLightAcc_DDirLightShadowed1`
and `GXLightAcc_DDirLightShadowed1_Cloud`. Decompiled, the diffuse term of light 0 (the sun) is

    clamp(dot(L0, N), 0, 1) / PI * color0 * shadow0

with `shadow0` built from `min(shadow.x, shadow.y)` of the screen-space shadow mask. A Wind Waker
look is those two smooth ramps turned into near-steps: a hard terminator and a hard shadow edge,
with the ambient and specular passes left alone.

The mechanism needs no DLL. vkd3d-proton reads `$VKD3D_SHADER_OVERRIDE/<hash>.spv` in place of its
own DXIL->SPIR-V conversion, where `<hash>` is FNV-1 64 (`h = h * 0x100000001b3 ^ byte`, seed
`0xcbf29ce484222325`) over the DXIL container bytes, printed `%016x`. Measured 2026-10-14: the
dumped name of `GXLightAcc_DDirLightShadowed1.ppo` was `6f88fcf1abe6b7d8`, the value computed from
the extracted member. The override has to be a patch of vkd3d's own SPIR-V for that shader --
dumped by `VKD3D_SHADER_DUMP_PATH` -- because vkd3d lowers resources to its bindless heap and root
constants, which a module compiled from source would not match.

Two rewrites, both located by structure and refused unless they match exactly:

* terminator -- the `OpDot` feeding `NClamp(dot, 0, 1)` whose result is multiplied by 1/PI. The
  first such dot is light 0. Its result is replaced by `smoothstep(lo, hi, dot) * 2 - 1`, so both
  the plain branch (`clamp`) and the subsurface branch (`max(dot + wrap, 0)`) see a step.
* shadow -- the `NMin` of components 0 and 1 of one image sample. Its result is replaced by
  `smoothstep(lo, hi, min)`.

Usage:
    python3 scripts/er-cel-shade.py --dump <vkd3d dump dir> --out <override dir>
    python3 scripts/er-cel-shade.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# The shaders this tool rewrites, by the debug name each DXIL container carries.
SUN_SHADERS = (
    "GXLightAcc_DDirLight1",
    "GXLightAcc_DDirLightShadowed1",
    "GXLightAcc_DDirLightShadowed1_Cloud",
)
# The passes that multiply ambient occlusion into the finished diffuse light buffer, which by then
# holds the sun, the tiled point and spot lights and the ambient term. Measured 2026-10-14 in the
# Raya Lucaria library: the sun pass draws the whole frame, but the room is lit almost entirely by
# ambient and local lights, so hardening the sun alone left it looking vanilla. Posterizing here is
# what reaches every light at once.
AO_SHADERS = (
    "GXLightDeferredAO",
    "GXLightDeferredAOWithSsr",
    "GXLightDeferredAOEnvMapRegionFresnelPreMultiply",
)
TARGET_SHADERS = SUN_SHADERS + AO_SHADERS
LUMA = (0.2126, 0.7152, 0.0722)

INV_PI = "0.318309873"
# spirv-as stamps the version its target environment implies, so the environment has to follow the
# module's own version word or the override silently becomes a different SPIR-V version.
ENV_FOR_VERSION = {0x10000: "vulkan1.0", 0x10300: "vulkan1.1", 0x10400: "vulkan1.1spv1.4",
                   0x10500: "vulkan1.2", 0x10600: "vulkan1.3"}
TOOL_TIMEOUT_SECONDS = 20


def fnv1_64(data: bytes) -> int:
    """vkd3d-proton's `vkd3d_shader_hash`: FNV-1 (multiply, then xor), 64-bit."""
    h = 0xCBF29CE484222325
    for byte in data:
        h = ((h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF) ^ byte
    return h


def dxil_debug_name(data: bytes) -> str | None:
    """The shader name in the container's debug path, e.g. `GXLightAcc_DDirLight1`."""
    match = re.search(rb"([A-Za-z0-9_\[\]@]+)\.[pvc]po\.pdb", data)
    return match.group(1).decode() if match else None


@dataclass(frozen=True)
class Params:
    terminator_lo: float
    terminator_hi: float
    shadow_lo: float
    shadow_hi: float
    harden_fill: bool
    band_stops: float = 1.0

    def check(self) -> None:
        if not self.band_stops > 0:
            raise ValueError("a posterize band must be wider than zero stops")
        if not self.terminator_lo < self.terminator_hi:
            raise ValueError("terminator band must have lo < hi (smoothstep is undefined otherwise)")
        if not self.shadow_lo < self.shadow_hi:
            raise ValueError("shadow band must have lo < hi (smoothstep is undefined otherwise)")


class PatchError(RuntimeError):
    pass


def run_tool(argv: list[str], stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(argv, input=stdin, capture_output=True, timeout=TOOL_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        raise PatchError(f"{argv[0]} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def disassemble(spv: Path) -> str:
    return run_tool(["spirv-dis", "--raw-id", "--no-header", "--no-color", str(spv)]).decode()


def target_env(spv: Path) -> str:
    version = int.from_bytes(spv.read_bytes()[4:8], "little")
    if version not in ENV_FOR_VERSION:
        raise PatchError(f"{spv.name}: no Vulkan environment for SPIR-V version {version:#x}")
    return ENV_FOR_VERSION[version]


def assemble(text: str, out: Path, env: str) -> None:
    run_tool(["spirv-as", "--preserve-numeric-ids", "--target-env", env, "-o", str(out), "-"],
             stdin=text.encode())


def validate(spv: Path, env: str) -> None:
    run_tool(["spirv-val", "--target-env", env, str(spv)])


def same_module(a: bytes, b: bytes) -> bool:
    """Equal apart from header words 2 and 3.

    spirv-as always writes its own generator id, and recomputes the bound as one past the highest
    id used, while dxil-spirv's bound for the sun pass is one higher than that (872 against 871).
    Neither word changes what the driver runs.
    """
    return len(a) == len(b) and a[:8] == b[:8] and a[16:] == b[16:]


DEF_RE = re.compile(r"^\s*(%\d+) = (Op\w+)(.*)$")


def parse_defs(lines: list[str]) -> dict[str, tuple[int, str, list[str]]]:
    defs = {}
    for index, line in enumerate(lines):
        match = DEF_RE.match(line)
        if match:
            defs[match.group(1)] = (index, match.group(2), match.group(3).split())
    return defs


def patch_text(text: str, params: Params) -> tuple[str, dict]:
    params.check()
    lines = text.split("\n")
    defs = parse_defs(lines)

    def single(pattern: str, what: str) -> str:
        found = [rid for rid, (_, op, args) in defs.items() if re.fullmatch(pattern, op + " " + " ".join(args))]
        if len(found) != 1:
            raise PatchError(f"expected exactly one {what}, found {len(found)}")
        return found[0]

    float_t = single(r"OpTypeFloat 32", "32-bit float type")
    glsl = single(r'OpExtInstImport "GLSL\.std\.450"', "GLSL.std.450 import")

    def float_const(value: str) -> str | None:
        for rid, (_, op, args) in defs.items():
            if op == "OpConstant" and args[0] == float_t and args[1] == value:
                return rid
        return None

    zero, one, inv_pi = float_const("0"), float_const("1"), float_const(INV_PI)
    if not (zero and one and inv_pi):
        raise PatchError("module lacks one of the float constants 0, 1, 1/PI")

    # NClamp(dot, 0, 1) whose result is scaled by 1/PI: a Lambert term.
    users_of_inv_pi = set()
    for _, (_, op, args) in defs.items():
        if op == "OpFMul" and inv_pi in args[1:]:
            users_of_inv_pi.update(a for a in args[1:] if a != inv_pi)
    lambert_dots = []
    for rid, (index, op, args) in sorted(defs.items(), key=lambda kv: kv[1][0]):
        if (op == "OpExtInst" and args[:3] == [float_t, glsl, "NClamp"] and args[4:] == [zero, one]
                and defs.get(args[3], (0, ""))[1] == "OpDot" and rid in users_of_inv_pi):
            lambert_dots.append(args[3])
    if len(lambert_dots) != 2:
        raise PatchError(f"expected the two Lambert dots (light 0 and light 1), found {len(lambert_dots)}")

    # NMin(sample.x, sample.y): the screen-space shadow mask.
    shadow_mins = []
    for rid, (_, op, args) in defs.items():
        if op != "OpExtInst" or args[:3] != [float_t, glsl, "NMin"]:
            continue
        a, b = (defs.get(x) for x in args[3:5])
        if not (a and b) or a[1] != "OpCompositeExtract" or b[1] != "OpCompositeExtract":
            continue
        if a[2][1] != b[2][1] or (a[2][2], b[2][2]) != ("0", "1"):
            continue
        if defs.get(a[2][1], (0, ""))[1].startswith("OpImageSample"):
            shadow_mins.append(rid)
    if len(shadow_mins) > 1:
        raise PatchError(f"expected at most one shadow-mask min, found {len(shadow_mins)}")

    next_id = max(int(rid) for rid in re.findall(r"%(\d+)", text)) + 1

    def fresh() -> str:
        nonlocal next_id
        rid = f"%{next_id}"
        next_id += 1
        return rid

    new_consts: list[str] = []

    def const(value: float) -> str:
        rid = fresh()
        new_consts.append(f"{rid} = OpConstant {float_t} {value!r}")
        return rid

    inserts: dict[int, list[str]] = {}

    def harden(rid: str, lo: float, hi: float, signed: bool) -> None:
        index, op, args = defs[rid]
        raw = fresh()
        lines[index] = lines[index].replace(f"{rid} = {op}", f"{raw} = {op}", 1)
        c_lo, c_hi = const(lo), const(hi)
        step = fresh() if signed else rid
        out = [f"{step} = OpExtInst {float_t} {glsl} SmoothStep {c_lo} {c_hi} {raw}"]
        if signed:
            two, doubled = const(2.0), fresh()
            out += [f"{doubled} = OpFMul {float_t} {step} {two}",
                    f"{rid} = OpFSub {float_t} {doubled} {one}"]
        inserts.setdefault(index, []).extend(out)

    harden(lambert_dots[0], params.terminator_lo, params.terminator_hi, signed=True)
    if params.harden_fill:
        harden(lambert_dots[1], params.terminator_lo, params.terminator_hi, signed=True)
    for rid in shadow_mins:
        harden(rid, params.shadow_lo, params.shadow_hi, signed=False)

    last_const = max(i for _, (i, op, args) in defs.items() if op == "OpConstant" and args[0] == float_t)
    inserts.setdefault(last_const, [])[0:0] = new_consts

    out_lines = []
    for index, line in enumerate(lines):
        out_lines.append(line)
        out_lines.extend("        " + extra for extra in inserts.get(index, []))
    report = {
        "terminator_dot": lambert_dots[0],
        "fill_dot": lambert_dots[1],
        "fill_hardened": params.harden_fill,
        "shadow_min": shadow_mins[0] if shadow_mins else None,
    }
    return "\n".join(out_lines), report


def colour_stores(lines: list[str], defs: dict, text: str) -> dict[int, tuple[int, str, str]]:
    """component -> (line index, pointer id, value id) for the x, y and z stores to `SV_Target`."""
    names = dict(re.findall(r'OpName (%\d+) "(\w+)"', text))
    target = next((rid for rid, name in names.items() if name == "SV_Target"), None)
    if target is None:
        raise PatchError("module has no output named SV_Target")
    int_ts = {rid for rid, (_, op, args) in defs.items() if op == "OpTypeInt" and args[0] == "32"}
    int_value = {rid: int(args[1]) for rid, (_, op, args) in defs.items() if op == "OpConstant" and args[0] in int_ts}
    component = {rid: int_value.get(args[2]) for rid, (_, op, args) in defs.items()
                 if op == "OpAccessChain" and len(args) == 3 and args[1] == target}
    stores = {}
    for index, line in enumerate(lines):
        match = re.match(r"^\s*OpStore (%\d+) (%\d+)$", line)
        if match and component.get(match.group(1)) in (0, 1, 2):
            if component[match.group(1)] in stores:
                raise PatchError("SV_Target has more than one store per component")
            stores[component[match.group(1)]] = (index, match.group(1), match.group(2))
    if len(stores) != 3:
        raise PatchError(f"expected three colour stores to SV_Target, found {len(stores)}")
    return stores


def terms_text(text: str) -> tuple[str, dict]:
    """Make `GXLightComposite` show where each pixel's light comes from instead of summing it.

    The pass computes `SV_Target = irradiance + specular + emissive`. This writes the green
    channel of each term to its own output channel -- diffuse light to red, specular to green,
    emissive to blue -- so a capture can be measured for which term lights which part of the frame.
    Green stands in for luminance because it carries most of it.

    The terms are told apart by the arithmetic, not by load order: the first sum is
    `specular.x + irradiance.x` and the second adds `emissive.x`, and only emissive has its `.w`
    read (it scales the second output).
    """
    lines = text.split("\n")
    defs = parse_defs(lines)
    fetches = [rid for rid, (_, op, _args) in defs.items() if op == "OpImageFetch"]
    if len(fetches) != 3:
        raise PatchError(f"expected the three light buffer fetches, found {len(fetches)}")
    extract = {}
    for rid, (_, op, args) in defs.items():
        if op == "OpCompositeExtract" and args[1] in fetches:
            extract[(args[1], int(args[2]))] = rid
    stores = colour_stores(lines, defs, text)
    x_sum = defs[stores[0][2]]
    # x is NMax(sum, 0); sum is FAdd(FAdd(specular.x, irradiance.x), emissive.x).
    if x_sum[1] != "OpExtInst" or "NMax" not in x_sum[2]:
        raise PatchError("SV_Target.x is not max(sum, 0)")
    outer = defs[x_sum[2][3]]
    if outer[1] != "OpFAdd":
        raise PatchError("SV_Target.x is not a sum")
    inner = defs[outer[2][1]]
    if inner[1] != "OpFAdd":
        raise PatchError("SV_Target.x is not a sum of three terms")
    source = {rid: fetch for (fetch, comp), rid in extract.items() if comp == 0}
    specular, irradiance, emissive = source.get(inner[2][1]), source.get(inner[2][2]), source.get(outer[2][2])
    if None in (specular, irradiance, emissive) or (emissive, 3) not in extract:
        raise PatchError("could not tell irradiance, specular and emissive apart")
    for component, fetch in zip((0, 1, 2), (irradiance, specular, emissive)):
        index, pointer, _ = stores[component]
        lines[index] = re.sub(r"OpStore %\d+ %\d+$", f"OpStore {pointer} {extract[(fetch, 1)]}", lines[index])
    return "\n".join(lines), {"probe": "terms", "irradiance": irradiance, "specular": specular,
                              "emissive": emissive}


def posterize_channels_text(text: str, params: Params) -> tuple[str, dict]:
    """Posterize every store to `SV_Target.xyz`, one channel at a time.

    For shaders that write their colour from more than one branch -- the forward hair shader
    does -- there is no single x/y/z triplet to take a luminance from, so each stored channel is
    rounded to whole steps of `band_stops` stops on its own. Hue can shift a little where the
    channels round differently; that is the price of not reconstructing the branches.
    """
    params.check()
    lines = text.split("\n")
    defs = parse_defs(lines)
    float_t = next(rid for rid, (_, op, args) in defs.items() if op == "OpTypeFloat" and args == ["32"])
    glsl = next(rid for rid, (_, op, args) in defs.items()
                if op == "OpExtInstImport" and args == ['"GLSL.std.450"'])
    names = dict(re.findall(r'OpName (%\d+) "(\w+)"', text))
    target = next((rid for rid, name in names.items() if name == "SV_Target"), None)
    if target is None:
        raise PatchError("module has no output named SV_Target")
    int_ts = {rid for rid, (_, op, args) in defs.items() if op == "OpTypeInt" and args[0] == "32"}
    int_value = {rid: int(args[1]) for rid, (_, op, args) in defs.items() if op == "OpConstant" and args[0] in int_ts}
    colour_pointers = {rid for rid, (_, op, args) in defs.items()
                       if op == "OpAccessChain" and len(args) == 3 and args[1] == target
                       and int_value.get(args[2]) in (0, 1, 2)}
    next_id = max(int(rid) for rid in re.findall(r"%(\d+)", text)) + 1

    def fresh() -> str:
        nonlocal next_id
        rid = f"%{next_id}"
        next_id += 1
        return rid

    consts = {name: fresh() for name in ("eps", "inv", "half", "step")}
    values = {"eps": 1e-6, "inv": 1.0 / params.band_stops, "half": 0.5, "step": params.band_stops}
    out_lines = []
    stores = 0
    for line in lines:
        match = re.match(r"^(\s*)OpStore (%\d+) (%\d+)$", line)
        if not (match and match.group(2) in colour_pointers):
            out_lines.append(line)
            continue
        indent, pointer, value = match.groups()
        m, lg, q, qh, fl, e, out = (fresh() for _ in range(7))
        out_lines += [indent + body for body in (
            f"{m} = OpExtInst {float_t} {glsl} NMax {value} {consts['eps']}",
            f"{lg} = OpExtInst {float_t} {glsl} Log2 {m}",
            f"{q} = OpFMul {float_t} {lg} {consts['inv']}",
            f"{qh} = OpFAdd {float_t} {q} {consts['half']}",
            f"{fl} = OpExtInst {float_t} {glsl} Floor {qh}",
            f"{e} = OpFMul {float_t} {fl} {consts['step']}",
            f"{out} = OpExtInst {float_t} {glsl} Exp2 {e}",
            f"OpStore {pointer} {out}",
        )]
        stores += 1
    if stores < 3:
        raise PatchError(f"expected colour stores to SV_Target, found {stores}")
    last_const = max(i for _, (i, op, args) in defs.items() if op == "OpConstant" and args[0] == float_t)
    out_lines[last_const + 1:last_const + 1] = [f"        {consts[k]} = OpConstant {float_t} {values[k]!r}"
                                                for k in consts]
    return "\n".join(out_lines), {"posterize_stops": params.band_stops, "stores_replaced": stores,
                                  "per_channel": True}


def posterize_text(text: str, params: Params) -> tuple[str, dict]:
    """Quantize the luminance of the stored `SV_Target.xyz` to whole steps of `band_stops` stops.

    `c * exp2(s * round(log2(L) / s)) / L`, with `L` the Rec. 709 luminance of `c`. Working in
    stops keeps the bands where the eye sees them whatever the exposure, and scaling all three
    channels by one factor keeps the hue.
    """
    params.check()
    lines = text.split("\n")
    defs = parse_defs(lines)
    float_t = next(rid for rid, (_, op, args) in defs.items() if op == "OpTypeFloat" and args == ["32"])
    glsl = next(rid for rid, (_, op, args) in defs.items()
                if op == "OpExtInstImport" and args == ['"GLSL.std.450"'])
    stores = colour_stores(lines, defs, text)
    first_store = min(index for index, _, _ in stores.values())
    for index, _, value in stores.values():
        if defs[value][0] > first_store:
            raise PatchError(f"{value} is computed after the first colour store")

    next_id = max(int(rid) for rid in re.findall(r"%(\d+)", text)) + 1

    def fresh() -> str:
        nonlocal next_id
        rid = f"%{next_id}"
        next_id += 1
        return rid

    consts = {name: fresh() for name in ("r", "g", "b", "eps", "inv", "half", "step")}
    values = {"r": LUMA[0], "g": LUMA[1], "b": LUMA[2], "eps": 1e-6,
              "inv": 1.0 / params.band_stops, "half": 0.5, "step": params.band_stops}
    x, y, z = (stores[c][2] for c in (0, 1, 2))
    wr, wg, l, lm, lg, q, qh, fl, e, lq, sc = (fresh() for _ in range(11))
    nx, ny, nz = fresh(), fresh(), fresh()
    body = [
        f"{wr} = OpFMul {float_t} {x} {consts['r']}",
        f"{wg} = OpExtInst {float_t} {glsl} Fma {y} {consts['g']} {wr}",
        f"{l} = OpExtInst {float_t} {glsl} Fma {z} {consts['b']} {wg}",
        f"{lm} = OpExtInst {float_t} {glsl} NMax {l} {consts['eps']}",
        f"{lg} = OpExtInst {float_t} {glsl} Log2 {lm}",
        f"{q} = OpFMul {float_t} {lg} {consts['inv']}",
        f"{qh} = OpFAdd {float_t} {q} {consts['half']}",
        f"{fl} = OpExtInst {float_t} {glsl} Floor {qh}",
        f"{e} = OpFMul {float_t} {fl} {consts['step']}",
        f"{lq} = OpExtInst {float_t} {glsl} Exp2 {e}",
        f"{sc} = OpFDiv {float_t} {lq} {lm}",
        f"{nx} = OpFMul {float_t} {x} {sc}",
        f"{ny} = OpFMul {float_t} {y} {sc}",
        f"{nz} = OpFMul {float_t} {z} {sc}",
    ]
    for component, new in zip((0, 1, 2), (nx, ny, nz)):
        index, pointer, _ = stores[component]
        lines[index] = re.sub(r"OpStore %\d+ %\d+$", f"OpStore {pointer} {new}", lines[index])
    lines[first_store:first_store] = ["        " + line for line in body]
    last_const = max(i for _, (i, op, args) in defs.items() if op == "OpConstant" and args[0] == float_t)
    lines[last_const + 1:last_const + 1] = [f"        {consts[k]} = OpConstant {float_t} {values[k]!r}"
                                            for k in consts]
    return "\n".join(lines), {"posterize_stops": params.band_stops, "stores_replaced": 3}


def probe_text(text: str) -> tuple[str, dict]:
    """Make the pass write constant magenta (8, 0, 8) to `SV_Target.xyz` on every pixel it draws.

    This answers whether the pass reaches the screen at all, independently of the light it would
    have computed: a pass that is drawn turns the frame magenta wherever albedo is not black, and
    one that is not drawn leaves the frame untouched.
    """
    lines = text.split("\n")
    defs = parse_defs(lines)
    names = dict(re.findall(r'OpName (%\d+) "(\w+)"', text))
    target = next((rid for rid, name in names.items() if name == "SV_Target"), None)
    if target is None:
        raise PatchError("module has no output named SV_Target")
    float_t = next(rid for rid, (_, op, args) in defs.items() if op == "OpTypeFloat" and args == ["32"])
    int_ts = {rid for rid, (_, op, args) in defs.items() if op == "OpTypeInt" and args[0] == "32"}
    int_value = {rid: int(args[1]) for rid, (_, op, args) in defs.items() if op == "OpConstant" and args[0] in int_ts}
    component = {rid: int_value.get(args[2]) for rid, (_, op, args) in defs.items()
                 if op == "OpAccessChain" and len(args) == 3 and args[1] == target}

    next_id = max(int(rid) for rid in re.findall(r"%(\d+)", text)) + 1
    eight, zero = f"%{next_id}", f"%{next_id + 1}"
    value_for = {0: eight, 1: zero, 2: eight}
    replaced = 0
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)OpStore (%\d+) (%\d+)$", line)
        if match and component.get(match.group(2)) in value_for:
            lines[index] = f"{match.group(1)}OpStore {match.group(2)} {value_for[component[match.group(2)]]}"
            replaced += 1
    if replaced != 3:
        raise PatchError(f"expected three colour stores to SV_Target, found {replaced}")
    last_const = max(i for _, (i, op, args) in defs.items() if op == "OpConstant" and args[0] == float_t)
    lines[last_const + 1:last_const + 1] = [f"        {eight} = OpConstant {float_t} 8",
                                            f"        {zero} = OpConstant {float_t} 0"]
    return "\n".join(lines), {"probe": "magenta", "stores_replaced": replaced}


def patch_module(src: Path, dst: Path, params: Params, mode: str = "sun") -> dict:
    env = target_env(src)
    text = disassemble(src)
    with tempfile.TemporaryDirectory() as tmp:
        roundtrip = Path(tmp) / "roundtrip.spv"
        assemble(text, roundtrip, env)
        if not same_module(roundtrip.read_bytes(), src.read_bytes()):
            raise PatchError(f"{src.name}: spirv-dis/spirv-as does not round-trip this module")
    validate(src, env)
    def posterize_any() -> tuple[str, dict]:
        try:
            return posterize_text(text, params)
        except PatchError as error:
            if "more than one store" not in str(error):
                raise
            return posterize_channels_text(text, params)

    rewrite = {"sun": lambda: patch_text(text, params), "ao": posterize_any,
               "probe": lambda: probe_text(text), "terms": lambda: terms_text(text)}[mode]
    patched, report = rewrite()
    assemble(patched, dst, env)
    validate(dst, env)
    return report


def build_overrides(dump: Path, out: Path, params: Params, probe: bool = False, terms: bool = False,
                    extra: tuple[str, ...] = ()) -> list[dict]:
    """`extra` names forward-lit material shaders to posterize as well. A forward shader computes
    its own lighting and writes finished colour, so it never passes through the deferred AO pass:
    measured 2026-10-14, the player's hair (`P[ChrCustomize][Hair]_FwdSdw`) reads `cbLight`, the
    shadow mask, the IBL cubes and the irradiance volumes itself. Posterizing its output bands the
    hair texture along with the light, because albedo is already in it."""
    out.mkdir(parents=True, exist_ok=True)
    results = []
    wanted = ("GXLightComposite",) if terms else SUN_SHADERS if probe else TARGET_SHADERS + tuple(extra)
    for dxil in sorted(dump.glob("*.dxil")):
        data = dxil.read_bytes()
        name = dxil_debug_name(data)
        if name not in wanted:
            continue
        digest = f"{fnv1_64(data):016x}"
        if digest != dxil.stem:
            raise PatchError(f"{dxil.name}: FNV-1 of its bytes is {digest}, so the hash model is wrong")
        spv = dump / f"{digest}.spv"
        mode = "terms" if terms else "probe" if probe else "sun" if name in SUN_SHADERS else "ao"
        report = patch_module(spv, out / spv.name, params, mode)
        results.append({"name": name, "hash": digest, "mode": mode, **report})
    if not results:
        raise PatchError(f"no target shader in {dump}; launch once with VKD3D_SHADER_DUMP_PATH set")
    manifest = {"params": None if probe or terms else params.__dict__, "probe": probe, "terms": terms,
                "shaders": results}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return results


SELFTEST_MODULE = """\
OpCapability Shader
%1 = OpExtInstImport "GLSL.std.450"
OpMemoryModel Logical GLSL450
OpEntryPoint Fragment %2 "main" %3 %4
OpExecutionMode %2 OriginUpperLeft
OpDecorate %3 Location 0
OpDecorate %4 Location 0
OpDecorate %5 DescriptorSet 0
OpDecorate %5 Binding 0
%6 = OpTypeVoid
%7 = OpTypeFunction %6
%8 = OpTypeFloat 32
%9 = OpTypeVector %8 3
%10 = OpTypeVector %8 4
%11 = OpTypeVector %8 2
%12 = OpTypePointer Input %9
%13 = OpTypePointer Output %10
%14 = OpTypeImage %8 2D 0 0 0 1 Unknown
%15 = OpTypeSampledImage %14
%16 = OpTypePointer UniformConstant %15
%17 = OpConstant %8 0
%18 = OpConstant %8 1
%19 = OpConstant %8 0.318309873
%3 = OpVariable %12 Input
%4 = OpVariable %13 Output
%5 = OpVariable %16 UniformConstant
%2 = OpFunction %6 None %7
%20 = OpLabel
%21 = OpLoad %9 %3
%22 = OpDot %8 %21 %21
%23 = OpDot %8 %21 %21
%24 = OpLoad %15 %5
%25 = OpCompositeConstruct %11 %17 %17
%26 = OpImageSampleImplicitLod %10 %24 %25
%27 = OpCompositeExtract %8 %26 0
%28 = OpCompositeExtract %8 %26 1
%29 = OpExtInst %8 %1 NMin %27 %28
%30 = OpExtInst %8 %1 NClamp %22 %17 %18
%31 = OpExtInst %8 %1 NClamp %23 %17 %18
%32 = OpFMul %8 %30 %19
%33 = OpFMul %8 %31 %19
%34 = OpFMul %8 %32 %29
%35 = OpFAdd %8 %34 %33
%36 = OpCompositeConstruct %10 %35 %35 %35 %18
OpStore %4 %36
OpReturn
OpFunctionEnd
"""


# The same module with its output written one component at a time, the way dxil-spirv emits it.
PROBE_SELFTEST_MODULE = (
    SELFTEST_MODULE
    .replace('OpDecorate %3 Location 0\n', 'OpName %4 "SV_Target"\nOpDecorate %3 Location 0\n')
    .replace("%19 = OpConstant %8 0.318309873\n",
             "%19 = OpConstant %8 0.318309873\n%37 = OpTypeInt 32 0\n%38 = OpConstant %37 0\n"
             "%39 = OpConstant %37 1\n%44 = OpConstant %37 2\n%45 = OpConstant %37 3\n"
             "%46 = OpTypePointer Output %8\n")
    .replace("%36 = OpCompositeConstruct %10 %35 %35 %35 %18\nOpStore %4 %36\n",
             "%40 = OpAccessChain %46 %4 %38\nOpStore %40 %35\n"
             "%41 = OpAccessChain %46 %4 %39\nOpStore %41 %35\n"
             "%42 = OpAccessChain %46 %4 %44\nOpStore %42 %35\n"
             "%43 = OpAccessChain %46 %4 %45\nOpStore %43 %35\n")
)


def measure_magenta(image: Path) -> dict:
    """Share of pixels in a capture where red and blue both clear green by a wide margin."""
    from PIL import Image  # run under `uv run --with pillow`

    picture = Image.open(image).convert("RGB")
    flatten = getattr(picture, "get_flattened_data", None) or picture.getdata
    pixels = list(flatten())
    magenta = sum(1 for r, g, b in pixels if r - g > 80 and b - g > 80)
    return {"image": str(image), "pixels": len(pixels), "magenta": magenta,
            "magenta_share": round(magenta / max(len(pixels), 1), 4)}


def selftest() -> int:
    for tool in ("spirv-as", "spirv-dis", "spirv-val"):
        if not shutil.which(tool):
            print(f"selftest: {tool} not on PATH", file=sys.stderr)
            return 1
    params = Params(0.0, 0.06, 0.4, 0.6, harden_fill=False)
    failures = []

    def expect(cond: bool, what: str) -> None:
        if not cond:
            failures.append(what)

    expect(f"{fnv1_64(b''):016x}" == "cbf29ce484222325", "FNV-1 of empty input is the seed")
    expect(f"{fnv1_64(b'a'):016x}" == "af63bd4c8601b7be", "FNV-1 64 of 'a' matches the published vector")
    expect(dxil_debug_name(b"..\\Dist\\GR\\WIN_D3D12_Pdb\\x\\GXLightAcc_DDirLight1.ppo.pdb\0")
           == "GXLightAcc_DDirLight1", "debug name is read out of the pdb path")

    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.spv", Path(tmp) / "out.spv"
        assemble(SELFTEST_MODULE, src, "vulkan1.1")
        report = patch_module(src, dst, params)
        expect(report["terminator_dot"] == "%22", "light 0 is the first Lambert dot")
        expect(report["fill_dot"] == "%23", "light 1 is the second Lambert dot")
        expect(report["shadow_min"] == "%29", "the shadow min is found")
        text = disassemble(dst)
        expect(text.count("SmoothStep") == 2, "one step for the terminator and one for the shadow")
        expect("%22 = OpFSub" in text, "the terminator id is redefined, so every user sees the step")
        expect("%29 = OpExtInst %8 %1 SmoothStep" in text, "the shadow id is redefined as a step")

        filled = patch_module(src, dst, Params(0.0, 0.06, 0.4, 0.6, harden_fill=True))
        expect(filled["fill_hardened"] and disassemble(dst).count("SmoothStep") == 3,
               "--harden-fill steps light 1 too")

        probe_src = Path(tmp) / "probe.spv"
        assemble(PROBE_SELFTEST_MODULE, probe_src, "vulkan1.1")
        probed = patch_module(probe_src, dst, params, "probe")
        probed_text = disassemble(dst)
        expect(probed["stores_replaced"] == 3, "the probe rewrites x, y and z")
        expect("OpStore %43 %35" in probed_text, "the alpha store is left alone")
        expect(probed_text.count("OpConstant %8 8") == 1, "the probe adds its magenta constant")

        posterized = patch_module(probe_src, dst, params, "ao")
        poster_text = disassemble(dst)
        expect(posterized["stores_replaced"] == 3, "the posterize rewrites x, y and z")
        expect(poster_text.count("Exp2") == 1 and poster_text.count("Log2") == 1,
               "one log/exp pair quantizes the luminance")
        expect("OpStore %43 %35" in poster_text, "the posterize leaves alpha alone")
        try:
            Params(0.0, 0.06, 0.4, 0.6, harden_fill=False, band_stops=0.0).check()
            failures.append("a zero-width posterize band must be refused")
        except ValueError:
            pass

        broken = SELFTEST_MODULE.replace("%31 = OpExtInst %8 %1 NClamp %23 %17 %18",
                                         "%31 = OpExtInst %8 %1 NClamp %23 %17 %17")
        try:
            patch_text(broken, params)
            failures.append("a module with one Lambert dot must be refused, not half-patched")
        except PatchError:
            pass
        try:
            Params(0.5, 0.5, 0.4, 0.6, harden_fill=False).check()
            failures.append("an empty smoothstep band must be refused")
        except ValueError:
            pass

    for failure in failures:
        print(f"selftest FAIL: {failure}", file=sys.stderr)
    print("selftest:", "FAIL" if failures else "ok")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dump", type=Path, help="VKD3D_SHADER_DUMP_PATH of a run that compiled the sun pass")
    parser.add_argument("--out", type=Path, help="directory to hand the game as VKD3D_SHADER_OVERRIDE")
    parser.add_argument("--terminator", type=float, nargs=2, default=(0.0, 0.06), metavar=("LO", "HI"),
                        help="smoothstep band on dot(L0, N) (default 0 0.06)")
    parser.add_argument("--shadow", type=float, nargs=2, default=(0.4, 0.6), metavar=("LO", "HI"),
                        help="smoothstep band on the shadow mask (default 0.4 0.6)")
    parser.add_argument("--harden-fill", action="store_true", help="step light 1 as well as the sun")
    parser.add_argument("--band-stops", type=float, default=1.0,
                        help="posterize band width in exposure stops for the diffuse light (default 1)")
    parser.add_argument("--probe-magenta", action="store_true",
                        help="instead of cel shading, make the pass write constant magenta, to prove it is drawn")
    parser.add_argument("--extra", nargs="*", default=(), metavar="NAME",
                        help="debug names of forward-lit shaders to posterize too, e.g. "
                             "'P[ChrCustomize][Hair]_FwdSdw'")
    parser.add_argument("--probe-terms", action="store_true",
                        help="instead of cel shading, make GXLightComposite show diffuse light as red, "
                             "specular as green and emissive as blue")
    parser.add_argument("--measure", type=Path, metavar="IMAGE",
                        help="print the magenta pixel share of a capture (needs `uv run --with pillow`)")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.measure:
        print(json.dumps(measure_magenta(args.measure)))
        return 0
    if not (args.dump and args.out):
        parser.error("--dump and --out are required")
    params = Params(*args.terminator, *args.shadow, harden_fill=args.harden_fill, band_stops=args.band_stops)
    try:
        params.check()
        results = build_overrides(args.dump, args.out, params, args.probe_magenta, args.probe_terms,
                                  tuple(args.extra))
    except (PatchError, ValueError) as error:
        print(f"er-cel-shade: {error}", file=sys.stderr)
        return 1
    for result in results:
        detail = {"probe": lambda r: f"probe stores={r['stores_replaced']}",
                  "terms": lambda r: f"terms irradiance={r['irradiance']} specular={r['specular']} "
                                     f"emissive={r['emissive']}",
                  "ao": lambda r: f"posterize {r['posterize_stops']} stop(s)",
                  "sun": lambda r: f"terminator={r['terminator_dot']} shadow={r['shadow_min']}"}[result["mode"]](result)
        print(f"{result['hash']}  {result['name']}  {detail}")
    print(f"override dir: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
