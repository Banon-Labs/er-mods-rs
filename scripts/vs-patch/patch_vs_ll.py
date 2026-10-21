"""Insert an object-space wobble into a game vertex shader's DXIL, as LLVM IR text.

Research prototype for the shader-editor vertex question (not wired into any gate). It reads the
text `dxc -dumpbin` prints, rewrites it, and writes text that libdxcompiler's `IDxcAssembler`
accepts (`scripts/vs-patch/dxa_lite.cpp`).

The edit, in IR terms:
- find the input element whose semantic is `POSITION` with index 0 in the entry point's signature
  metadata, and every `dx.op.loadInput.f32` of that element;
- after the last of those loads, create a handle to a new constant buffer at `b0, space42` (the
  shader editor's parameter block), read `er_p[3].x` (time) and one layer register
  (amplitude, frequency, speed);
- displace: `x += amp * sin(y * freq + time * speed)`, and the same on z with x and y swapped;
- replace every later use of the loaded x and z with the displaced values.

Every pass of one material reads the same `POSITION` element and runs the same chain after it, so
inserting the identical sequence at the load keeps the passes' positions computed the same way.

New values are named (`%er_*`), so the numbered values of the original keep their numbers.

With `--previous`, a velocity-pass shader also gets the wobble at the previous frame's time
(`er_p[32].x`) on the output that carries the previous clip position, so motion vectors carry the
wobble's own motion. That output is the one whose backward slice reads `VC_PrevMatrixViewProj`
(`b8` registers 29-32) and not `VC_MatrixViewProj` (24-27). Its slice reads either `POSITION0`
(the same input as the current position) or `POSITION1` (a previous-position stream). Every
instruction of that slice that depends on a displaced input is cloned right after itself with the
input replaced by its previous-time displacement, and the output's stores take the clones. A
clone right after its original is dominated by everything the original is, so this holds across
blocks and for phis.

Usage: patch_vs_ll.py <in.ll> [--layer-register N] [--no-cbuffer] [--previous] > out.ll
Exit status 3 means the shader has no float `POSITION` load this patch understands.
"""
from __future__ import annotations

import argparse
import re
import sys

ER_SPACE = 42
ER_REGISTER = 0
ER_TYPE = "%er.cbEditorParams"
ER_TYPE_DEF = f"{ER_TYPE} = type {{ [1024 x <4 x float>] }}"
ER_CB_BYTES = 16384
TIME_REGISTER = 3
# Previous frame's time, in the design's reserved range `er_p[32..63]`.
PREVIOUS_TIME_REGISTER = 32
SCENE_REGISTER = 8
CURRENT_VIEW_PROJ = {24, 25, 26, 27}
PREVIOUS_VIEW_PROJ = {29, 30, 31, 32}

LOAD_INPUT = re.compile(
    r"^\s*(%[\w.]+) = call float @dx\.op\.loadInput\.f32\(i32 4, i32 (\d+), i32 0, i8 (\d), i32 [^)]*\)")


class Unsupported(Exception):
    pass


def metadata_nodes(text: str) -> dict[int, str]:
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"^!(\d+) = (.*)$", text, re.M)}


def refs(node: str) -> list[str]:
    """The operands of a metadata tuple `!{a, b, ...}`, at the top level."""
    inner = node.strip()
    if not inner.startswith("!{"):
        raise Unsupported(f"not a tuple: {node[:60]}")
    inner = inner[2:-1]
    out, depth, cur = [], 0, ""
    for ch in inner:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def node_id(ref: str) -> int:
    m = re.fullmatch(r"!(\d+)", ref)
    if not m:
        raise Unsupported(f"expected a node reference, got {ref}")
    return int(m.group(1))


def position_element(text: str, nodes: dict[int, str], index: int = 0) -> int:
    """The input element id of `POSITION<index>` in the entry point's signature."""
    entry = re.search(r"^!dx\.entryPoints = !\{!(\d+)\}", text, re.M)
    if not entry:
        raise Unsupported("no dx.entryPoints")
    ep = refs(nodes[int(entry.group(1))])
    sigs = ep[2]
    if sigs == "null":
        raise Unsupported("no signatures")
    inputs = refs(nodes[node_id(sigs)])[0]
    if inputs == "null":
        raise Unsupported("no input signature")
    for element_ref in refs(nodes[node_id(inputs)]):
        element = refs(nodes[node_id(element_ref)])
        name = element[1]
        semantic_indexes = refs(nodes[node_id(element[4])])
        if name == '!"POSITION"' and semantic_indexes[0] == f"i32 {index}":
            return int(element[0].split()[1])
    raise Unsupported(f"no POSITION{index} input")


def add_cbuffer(text: str, nodes: dict[int, str]) -> tuple[str, int]:
    """Declare `b0, space42` in `!dx.resources` and return the text and the new range id."""
    res = re.search(r"^!dx\.resources = !\{!(\d+)\}", text, re.M)
    next_node = max(nodes) + 1
    new_lines = []
    if res:
        res_id = int(res.group(1))
        srv, uav, cbv, smp = refs(nodes[res_id])
        if cbv == "null":
            cb_list = []
        else:
            cb_list = refs(nodes[node_id(cbv)])
        used = [int(refs(nodes[node_id(r)])[0].split()[1]) for r in cb_list]
        range_id = (max(used) + 1) if used else 0
        cb_node = next_node
        new_lines.append(
            f'!{cb_node} = !{{i32 {range_id}, {ER_TYPE}* undef, !"", i32 {ER_SPACE}, '
            f"i32 {ER_REGISTER}, i32 1, i32 {ER_CB_BYTES}, null}}")
        list_node = next_node + 1
        new_lines.append(f"!{list_node} = !{{{', '.join(cb_list + [f'!{cb_node}'])}}}")
        text = re.sub(rf"^!{res_id} = .*$",
                      f"!{res_id} = !{{{srv}, {uav}, !{list_node}, {smp}}}", text, count=1,
                      flags=re.M)
    else:
        raise Unsupported("no dx.resources; adding one also means editing the entry point")
    text = text.rstrip("\n") + "\n" + "\n".join(new_lines) + "\n"
    first_type = re.search(r"^%[\w.]+ = type ", text, re.M)
    if first_type:
        text = text[:first_type.start()] + ER_TYPE_DEF + "\n" + text[first_type.start():]
    else:
        raise Unsupported("no type block to extend")
    return text, range_id


def ensure_declared(text: str, name: str, decl: str) -> str:
    if re.search(rf"^declare [^@]*@{re.escape(name)}\(", text, re.M):
        return text
    attr = re.search(r"^declare float @dx\.op\.loadInput\.f32\([^)]*\) (#\d+)", text, re.M)
    suffix = f" {attr.group(1)}" if attr else ""
    first_decl = re.search(r"^; Function Attrs:.*\n^declare |^declare ", text, re.M)
    return text[:first_decl.start()] + decl + suffix + "\n\n" + text[first_decl.start():]


def patch(text: str, layer_register: int, use_cbuffer: bool) -> str:
    nodes = metadata_nodes(text)
    pos_id = position_element(text, nodes)
    lines = text.split("\n")
    loads: dict[int, tuple[int, str]] = {}
    for index, line in enumerate(lines):
        m = LOAD_INPUT.match(line)
        if m and int(m.group(2)) == pos_id:
            loads[int(m.group(3))] = (index, m.group(1))
    if 0 not in loads or 1 not in loads or 2 not in loads:
        raise Unsupported(f"POSITION loads found for columns {sorted(loads)} only")
    defines = [i for i, l in enumerate(lines) if l.startswith("define ")]
    if len(defines) != 1:
        raise Unsupported("more than one function")
    body_end = next(i for i in range(defines[0], len(lines)) if lines[i] == "}")
    labels = [i for i in range(defines[0], body_end) if re.match(r"^[\w.]+:", lines[i])
              or re.match(r"^; <label>:\d+", lines[i])]
    last = max(index for index, _ in loads.values())
    if any(defines[0] < label < last for label in labels):
        raise Unsupported("POSITION is loaded after the first basic block")
    x, y, z = loads[0][1], loads[1][1], loads[2][1]

    if use_cbuffer:
        text_with_cb, range_id = add_cbuffer(text, nodes)
        handle = (f"  %er_h = call %dx.types.Handle @dx.op.createHandle(i32 57, i8 2, "
                  f"i32 {range_id}, i32 {ER_REGISTER}, i1 false)")
        time_src = [
            handle,
            f"  %er_r3 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, "
            f"%dx.types.Handle %er_h, i32 {TIME_REGISTER})",
            "  %er_time = extractvalue %dx.types.CBufRet.f32 %er_r3, 0",
            f"  %er_rl = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, "
            f"%dx.types.Handle %er_h, i32 {layer_register})",
            "  %er_amp = extractvalue %dx.types.CBufRet.f32 %er_rl, 0",
            "  %er_freq = extractvalue %dx.types.CBufRet.f32 %er_rl, 1",
            "  %er_speed = extractvalue %dx.types.CBufRet.f32 %er_rl, 2",
        ]
    else:
        text_with_cb = text
        time_src = [
            "  %er_time = fadd fast float 0.000000e+00, 0.000000e+00",
            "  %er_amp = fadd fast float 0.000000e+00, 0x3FB99999A0000000",
            "  %er_freq = fadd fast float 0.000000e+00, 2.000000e+00",
            "  %er_speed = fadd fast float 0.000000e+00, 1.000000e+00",
        ]
    wobble = time_src + [
        f"  %er_phx0 = fmul fast float {y}, %er_freq",
        "  %er_phx = call float @dx.op.tertiary.f32(i32 46, float %er_time, float %er_speed, "
        "float %er_phx0)",
        "  %er_sx = call float @dx.op.unary.f32(i32 13, float %er_phx)",
        f"  %er_x = call float @dx.op.tertiary.f32(i32 46, float %er_sx, float %er_amp, "
        f"float {x})",
        f"  %er_phz0 = fmul fast float {x}, %er_freq",
        "  %er_phz = call float @dx.op.tertiary.f32(i32 46, float %er_time, float %er_speed, "
        "float %er_phz0)",
        "  %er_sz = call float @dx.op.unary.f32(i32 12, float %er_phz)",
        f"  %er_z = call float @dx.op.tertiary.f32(i32 46, float %er_sz, float %er_amp, "
        f"float {z})",
    ]
    lines = text_with_cb.split("\n")
    # Recompute indexes in the edited text: the type and metadata edits moved lines.
    loads2 = {}
    for index, line in enumerate(lines):
        m = LOAD_INPUT.match(line)
        if m and int(m.group(2)) == pos_id:
            loads2[int(m.group(3))] = index
    last = max(loads2.values())
    body_end = next(i for i in range(last, len(lines)) if lines[i] == "}")
    for index in range(last + 1, body_end):
        line = lines[index]
        code, sep, comment = line.partition(";")
        code = re.sub(rf"{re.escape(x)}(?![\w.])", "%er_x", code)
        code = re.sub(rf"{re.escape(z)}(?![\w.])", "%er_z", code)
        lines[index] = code + sep + comment
    lines[last + 1:last + 1] = wobble
    out = "\n".join(lines)
    out = ensure_declared(out, "dx.op.unary.f32", "declare float @dx.op.unary.f32(i32, float)")
    out = ensure_declared(out, "dx.op.tertiary.f32",
                          "declare float @dx.op.tertiary.f32(i32, float, float, float)")
    if use_cbuffer:
        out = ensure_declared(
            out, "dx.op.createHandle",
            "declare %dx.types.Handle @dx.op.createHandle(i32, i8, i32, i32, i1)")
        out = ensure_declared(
            out, "dx.op.cbufferLoadLegacy.f32",
            "declare %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32, %dx.types.Handle, "
            "i32)")
        if "%dx.types.CBufRet.f32 = type" not in out:
            first_type = re.search(r"^%[\w.]+ = type ", out, re.M)
            out = (out[:first_type.start()] + "%dx.types.CBufRet.f32 = type { float, float, "
                   "float, float }\n" + out[first_type.start():])
        if "%dx.types.Handle = type" not in out:
            first_type = re.search(r"^%[\w.]+ = type ", out, re.M)
            out = (out[:first_type.start()] + "%dx.types.Handle = type { i8* }\n"
                   + out[first_type.start():])
    return out


VALUE = re.compile(r"%[\w.]+")
STORE_OUTPUT = re.compile(
    r"^(\s*call void @dx\.op\.storeOutput\.f32\(i32 5, i32 (\d+), i32 [^,]+, i8 \d, float )"
    r"([^)]+)(\).*)$")
SCENE_HANDLE = re.compile(
    rf"^\s*(%[\w.]+) = call %dx\.types\.Handle @dx\.op\.createHandle\(i32 57, i8 2, i32 \d+, "
    rf"i32 {SCENE_REGISTER}, ")
CB_LOAD = re.compile(r"cbufferLoadLegacy\.\w+\(i32 59, %dx\.types\.Handle (%[\w.]+), i32 (\d+)\)")


def wobble_lines(prefix: str, x: str, y: str, z: str, time: str) -> list[str]:
    """The same displacement `patch` inserts, at another time, defining `%<prefix>x/z`."""
    return [
        f"  %{prefix}phx0 = fmul fast float {y}, %er_freq",
        f"  %{prefix}phx = call float @dx.op.tertiary.f32(i32 46, float {time}, "
        f"float %er_speed, float %{prefix}phx0)",
        f"  %{prefix}sx = call float @dx.op.unary.f32(i32 13, float %{prefix}phx)",
        f"  %{prefix}x = call float @dx.op.tertiary.f32(i32 46, float %{prefix}sx, "
        f"float %er_amp, float {x})",
        f"  %{prefix}phz0 = fmul fast float {x}, %er_freq",
        f"  %{prefix}phz = call float @dx.op.tertiary.f32(i32 46, float {time}, "
        f"float %er_speed, float %{prefix}phz0)",
        f"  %{prefix}sz = call float @dx.op.unary.f32(i32 12, float %{prefix}phz)",
        f"  %{prefix}z = call float @dx.op.tertiary.f32(i32 46, float %{prefix}sz, "
        f"float %er_amp, float {z})",
    ]


def patch_previous(original: str, text: str) -> tuple[str, dict]:
    """Give the previous-position output the wobble at the previous frame's time.

    `original` is the shader before `patch`, `text` after it. Returns the new text and counts.
    """
    nodes = metadata_nodes(original)
    pos0 = position_element(original, nodes, 0)
    try:
        pos1 = position_element(original, nodes, 1)
    except Unsupported:
        pos1 = None
    lines = text.split("\n")
    define = next(i for i, l in enumerate(lines) if l.startswith("define "))
    body_end = next(i for i in range(define, len(lines)) if lines[i] == "}")
    defs: dict[str, int] = {}
    for index in range(define + 1, body_end):
        m = re.match(r"^\s*(%[\w.]+) = ", lines[index])
        if m:
            defs[m.group(1)] = index
    scene = {m.group(1) for l in lines[define:body_end] if (m := SCENE_HANDLE.match(l))}

    def operands(index: int) -> list[str]:
        code = lines[index].split(";", 1)[0]
        rhs = code.split("=", 1)[1] if re.match(r"^\s*%[\w.]+ = ", code) else code
        return VALUE.findall(rhs)

    stores: dict[int, list[int]] = {}
    for index in range(define + 1, body_end):
        m = STORE_OUTPUT.match(lines[index].split(";", 1)[0])
        if m:
            stores.setdefault(int(m.group(2)), []).append(index)

    def backward(indexes: list[int]) -> tuple[set[str], set[int]]:
        seen: set[str] = set()
        regs: set[int] = set()
        stack = [v for i in indexes for v in operands(i)]
        while stack:
            v = stack.pop()
            if v in seen or v not in defs:
                continue
            seen.add(v)
            cb = CB_LOAD.search(lines[defs[v]])
            if cb and cb.group(1) in scene:
                regs.add(int(cb.group(2)))
            stack.extend(operands(defs[v]))
        return seen, regs

    previous = []
    for element, indexes in stores.items():
        values, regs = backward(indexes)
        if regs & PREVIOUS_VIEW_PROJ and not regs & CURRENT_VIEW_PROJ:
            previous.append((element, indexes, values))
    if len(previous) != 1:
        raise Unsupported(f"{len(previous)} previous-position outputs")
    element, store_lines, slice_values = previous[0]

    # Sources: the current-time displaced POSITION0 values, and POSITION1's own loads.
    source: dict[str, str] = {"%er_x": "%er_px", "%er_z": "%er_pz"}
    block_at = defs["%er_z"]
    loads1: dict[int, tuple[int, str]] = {}
    if pos1 is not None:
        for index in range(define + 1, body_end):
            m = LOAD_INPUT.match(lines[index])
            if m and int(m.group(2)) == pos1:
                loads1[int(m.group(3))] = (index, m.group(1))
    uses_pos1 = any(v in slice_values for _, v in loads1.values())
    if uses_pos1:
        if sorted(loads1) != [0, 1, 2]:
            raise Unsupported(f"POSITION1 loads for columns {sorted(loads1)} only")
        if max(i for i, _ in loads1.values()) > block_at:
            raise Unsupported("POSITION1 is loaded after the wobble block")
        source[loads1[0][1]] = "%er_p1x"
        source[loads1[2][1]] = "%er_p1z"

    # Forward closure of the sources, restricted to the previous slice. Repeated to a fixed
    # point, because a loop phi names a value defined later in the text.
    tainted: set[str] = set(source)
    grew = True
    while grew:
        grew = False
        for index in range(define + 1, body_end):
            m = re.match(r"^\s*(%[\w.]+) = ", lines[index])
            if not m or m.group(1) in tainted:
                continue
            if any(v in tainted for v in operands(index)):
                tainted.add(m.group(1))
                grew = True
    clones = sorted(defs[v] for v in tainted - set(source) if v in slice_values)
    for index in clones:
        if index <= block_at:
            raise Unsupported("a previous-slice instruction precedes the wobble block")

    def clone_name(value: str) -> str:
        return "%er_pv_" + value[1:]

    rename = dict(source)
    rename.update({lines[i].split("=", 1)[0].strip(): clone_name(lines[i].split("=", 1)[0].strip())
                   for i in clones})

    def substitute(code: str) -> str:
        return VALUE.sub(lambda m: rename.get(m.group(0), m.group(0)), code)

    inserts: dict[int, list[str]] = {}
    for index in clones:
        code, sep, comment = lines[index].partition(";")
        inserts.setdefault(index, []).append(substitute(code).rstrip())
    for index in store_lines:
        code, sep, comment = lines[index].partition(";")
        m = STORE_OUTPUT.match(code)
        value = m.group(3).strip()
        lines[index] = m.group(1) + rename.get(value, value) + m.group(4) + sep + comment

    x0 =re.search(r"%er_phz0 = fmul fast float (%[\w.]+), %er_freq", text).group(1)
    y0 = re.search(r"%er_phx0 = fmul fast float (%[\w.]+), %er_freq", text).group(1)
    z0 = re.search(r"%er_z = call float @dx\.op\.tertiary\.f32\(i32 46, float %er_sz, "
                   r"float %er_amp, float (%[\w.]+)\)", text).group(1)
    block = [
        f"  %er_rp = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, "
        f"%dx.types.Handle %er_h, i32 {PREVIOUS_TIME_REGISTER})",
        "  %er_ptime = extractvalue %dx.types.CBufRet.f32 %er_rp, 0",
    ] + wobble_lines("er_p", x0, y0, z0, "%er_ptime")
    if uses_pos1:
        block += wobble_lines("er_p1", loads1[0][1], loads1[1][1], loads1[2][1], "%er_ptime")
    inserts.setdefault(block_at, [])[0:0] = block
    out: list[str] = []
    for index, line in enumerate(lines):
        out.append(line)
        out.extend(inserts.get(index, []))
    return "\n".join(out), {"element": element, "cloned": len(clones), "position1": uses_pos1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--layer-register", type=int, default=256 + 1)
    parser.add_argument("--no-cbuffer", action="store_true")
    parser.add_argument("--previous", action="store_true")
    args = parser.parse_args()
    text = open(args.input, encoding="utf-8").read()
    try:
        out = patch(text, args.layer_register, not args.no_cbuffer)
        if args.previous:
            if args.no_cbuffer:
                raise Unsupported("--previous reads the previous time from the parameter block")
            out, counts = patch_previous(text, out)
            print(f"previous: {counts}", file=sys.stderr)
    except Unsupported as error:
        print(f"unsupported: {error}", file=sys.stderr)
        return 3
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
