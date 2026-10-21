"""Which vertex inputs feed the current and the previous clip position of each velocity-pass shader.

Research prototype for the shader-editor vertex question. A wobble that should show in motion
vectors must be applied to the current position at this frame's time and to the previous position
at the previous frame's time. `velo_outputs.py` names the output that carries the previous
position (it reads `VC_PrevMatrixViewProj`, `b8` registers 29-32, and not `VC_MatrixViewProj`);
this prints, for that output and for `SV_Position`, the input semantics their backward slices load.

Usage: velo_inputs.py <a.ll> [<b.ll> ...]   prints one line per file:
    <path>\tcur=<semantics>\tprev=<semantics>
"""
from __future__ import annotations

import re
import sys

ASSIGN = re.compile(r"^\s*(%[\w.]+) = (.*)$")
STORE = re.compile(r"^\s*call void @dx\.op\.storeOutput\.\w+\(i32 5, i32 (\d+), i32 [^,]+, i8 (\d), "
                   r"\w+ ([^)]+)\)")
LOAD = re.compile(r"loadInput\.\w+\(i32 4, i32 (\d+), ")
CB_LOAD = re.compile(r"cbufferLoadLegacy\.\w+\(i32 59, %dx\.types\.Handle (%[\w.]+), i32 (\d+)\)")
HANDLE = re.compile(r"createHandle\(i32 57, i8 2, i32 (\d+), i32 (\d+),")


def table(text: str, header: str) -> list[str]:
    """Element names `<semantic><index>` of one signature table in the dumpbin comment block."""
    part = text.split(f"; {header} signature:", 1)[1].split(";\n;\n", 1)[0]
    return [f"{m.group(1)}{m.group(2)}" for m in re.finditer(
        r"^; (\w+)\s+(\d+)\s+(\w+)\s+(\d+)\s+(\w+)\s+(\w+)", part, re.M)]


def analyse(text: str) -> tuple[str, str]:
    inputs = table(text, "Input")
    outputs = table(text, "Output")
    body = text[text.index("\ndefine "):]
    body = body[:body.index("\n}\n")]
    defs: dict[str, str] = {}
    last: dict[int, list[str]] = {}
    for line in body.split("\n"):
        code = line.split(";", 1)[0]
        m = ASSIGN.match(code)
        if m:
            defs[m.group(1)] = m.group(2)
        s = STORE.match(code)
        if s:
            last.setdefault(int(s.group(1)), []).append(s.group(3).strip().split()[-1])

    def slice_of(element: int) -> tuple[set[str], set[int]]:
        semantics: set[str] = set()
        scene: set[int] = set()
        stack = list(last.get(element, []))
        seen: set[str] = set()
        while stack:
            v = stack.pop()
            if v in seen or v not in defs:
                continue
            seen.add(v)
            rhs = defs[v]
            m = LOAD.search(rhs)
            if m:
                semantics.add(inputs[int(m.group(1))])
            cb = CB_LOAD.search(rhs)
            if cb:
                h = HANDLE.search(defs.get(cb.group(1), ""))
                # cbSceneParam is `b8` in these shaders; register 24-27 current, 29-32 previous.
                if h and int(h.group(2)) == 8:
                    scene.add(int(cb.group(2)))
            stack.extend(re.findall(r"%[\w.]+", rhs))
        return semantics, scene

    cur = prev = "-"
    for element in sorted(last):
        semantics, scene = slice_of(element)
        name = outputs[element] if element < len(outputs) else f"#{element}"
        if name.startswith("SV_Position"):
            cur = ",".join(sorted(semantics))
        elif scene & {29, 30, 31, 32} and not scene & {24, 25, 26, 27}:
            prev = ",".join(sorted(semantics))
    return cur, prev


def main() -> int:
    for path in sys.argv[1:]:
        try:
            cur, prev = analyse(open(path, encoding="utf-8").read())
        except (OSError, ValueError, KeyError, IndexError) as error:
            cur = prev = f"error:{type(error).__name__}"
        print(f"{path}\tcur={cur}\tprev={prev}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
