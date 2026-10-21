"""Make SPIRV-Cross's HLSL from a dxil-spirv lift link like the game's original vertex shader.

Research prototype for the shader-editor vertex question. SPIRV-Cross names every varying
`TEXCOORD<location>` and puts `SV_Position` last; D3D12 links a vertex shader to the input layout
and to the next stage by semantic name and register, so both must be restored from the original
container's `ISG1` and `OSG1`. It also folds SPIRV-Cross's base-instance emulation away: the lift
computes `InstanceIndex - BaseInstance`, which is D3D's `SV_InstanceID`, so the base is 0 here.

With `--wobble`, inserts the same object-space wobble `patch_vs_ll.py` inserts in DXIL, reading a
constant buffer at `b0, space42`.

With `--no-precise`, drops the `precise` SPIRV-Cross puts on `SV_Position`.

Usage: fix_lift_hlsl.py <original.dxil> <lifted.hlsl> [--wobble] [--no-precise] > fixed.hlsl
Exit status 3: a varying this script cannot map one to one (two semantics packed in one register).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SPEC = importlib.util.spec_from_file_location("bcs", REPO / "scripts" / "build-cel-shaders.py")
bcs = importlib.util.module_from_spec(SPEC)
sys.modules["bcs"] = bcs
SPEC.loader.exec_module(bcs)

WOBBLE_CB = """cbuffer er_cbEditorParams : register(b0, space42)
{
    float4 er_p[1024];
};
"""


class Unsupported(Exception):
    pass


def semantic(element) -> str:
    if element.system_value == 1:
        return "SV_Position"
    if element.name.upper().startswith("SV_"):
        return element.name if element.semantic_index == 0 else (
            f"{element.name}{element.semantic_index}")
    return f"{element.name}{element.semantic_index}"


def by_register(elements, what: str) -> dict[int, object]:
    out = {}
    for element in elements:
        if element.register in out:
            raise Unsupported(f"{what}: two elements share register {element.register}")
        out[element.register] = element
    return out


def fix_struct(text: str, struct: str, elements, what: str, reorder: bool) -> str:
    m = re.search(rf"struct {struct}\n\{{\n(.*?)\n\}};", text, re.S)
    if not m:
        raise Unsupported(f"no {struct}")
    regs = by_register([e for e in elements if e.system_value in (0, 1)], what)
    members = m.group(1).split("\n")
    fixed = []
    for member in members:
        mm = re.match(r"^(\s*)(.*?) : TEXCOORD(\d+);$", member)
        if mm:
            loc = int(mm.group(3))
            if loc not in regs:
                raise Unsupported(f"{what}: location {loc} has no original element")
            element = regs[loc]
            fixed.append((element.register, f"{mm.group(1)}{mm.group(2)} : {semantic(element)};"))
            continue
        pm = re.match(r"^(\s*)(.*?) : SV_Position;$", member)
        if pm:
            pos = [e for e in elements if e.system_value == 1]
            fixed.append((pos[0].register if pos else 0, member))
            continue
        fixed.append((-1, member))
    if reorder:
        system_last = [f for f in fixed if f[0] < 0]
        fixed = sorted([f for f in fixed if f[0] >= 0], key=lambda f: f[0]) + system_last
    body = "\n".join(line for _, line in fixed)
    return text[:m.start(1)] + body + text[m.end(1):]


def fix(original: bytes, text: str, wobble: bool, keep_fast: bool) -> str:
    container = bcs.parse_container(original)
    inputs = bcs.parse_signature(container.required(b"ISG1"), "ISG1")
    outputs = bcs.parse_signature(container.required(b"OSG1"), "OSG1")
    text = re.sub(r"cbuffer SPIRV_Cross_VertexInfo\n\{.*?\n\};\n", "", text, flags=re.S)
    text = text.replace("SPIRV_Cross_BaseInstance", "0").replace("SPIRV_Cross_BaseVertex", "0")
    text = fix_struct(text, "SPIRV_Cross_Input", inputs, "ISG1", reorder=False)
    text = fix_struct(text, "SPIRV_Cross_Output", outputs, "OSG1", reorder=True)
    if keep_fast:
        # SPIRV-Cross writes `precise` for an `Invariant` position; the game's own shaders carry
        # `fast` on that chain, so keeping `precise` changes what the driver may fuse.
        text = re.sub(r"^(\s*)precise (float4 \w+ : SV_Position;)$", r"\1\2", text, flags=re.M)
    if wobble:
        pos = [e for e in inputs if e.name == "POSITION" and e.semantic_index == 0]
        if not pos:
            raise Unsupported("no POSITION0 input")
        m = re.search(r"^(\s*)(\w+) = stage_input\.(\w+);$", text, re.M)
        member = re.search(r"^\s*float3 (\w+) : POSITION0;$", text, re.M)
        if not member:
            raise Unsupported("POSITION0 is not a float3 input member")
        name = member.group(1)
        assign = re.search(rf"^(\s*){name} = stage_input\.{name};$", text, re.M)
        if not assign or not m:
            raise Unsupported("no copy of the POSITION input to wobble")
        indent = assign.group(1)
        insert = (
            f"\n{indent}{{\n"
            f"{indent}    float4 er_l = er_p[257];\n"
            f"{indent}    float er_t = er_p[3].x;\n"
            f"{indent}    float er_x0 = {name}.x;\n"
            f"{indent}    {name}.x = mad(sin(mad(er_t, er_l.z, {name}.y * er_l.y)), er_l.x, "
            f"{name}.x);\n"
            f"{indent}    {name}.z = mad(cos(mad(er_t, er_l.z, er_x0 * er_l.y)), er_l.x, "
            f"{name}.z);\n"
            f"{indent}}}")
        text = text[:assign.end()] + insert + text[assign.end():]
        text = WOBBLE_CB + "\n" + text
    return text


def main() -> int:
    original = open(sys.argv[1], "rb").read()
    text = open(sys.argv[2], encoding="utf-8").read()
    try:
        out = fix(original, text, "--wobble" in sys.argv[3:], "--no-precise" in sys.argv[3:])
    except Unsupported as error:
        print(f"unsupported: {error}", file=sys.stderr)
        return 3
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
