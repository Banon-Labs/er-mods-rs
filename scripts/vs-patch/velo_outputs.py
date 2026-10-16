"""Which outputs of a vertex shader carry the current and the previous clip position.

Research prototype for the shader-editor vertex question. For each output element of each `.ll`
given, walks the backward slice of its last stores and records which `cbSceneParam` (`b8`)
registers it reads: 24-27 are `VC_MatrixViewProj` and 29-32 `VC_PrevMatrixViewProj` in the
reflection `dxc -dumpbin` prints for these shaders. An output that reads 29-32 and not 24-27 is the
previous position a motion-vector pass needs; a wobble that should show up in motion vectors has
to be evaluated at the previous frame's time on exactly that slice.

Usage: velo_outputs.py <a.ll> [<b.ll> ...]   prints one line per file
"""
from __future__ import annotations

import re
import sys

STORE = re.compile(r"^\s*call void @dx\.op\.storeOutput\.\w+\(i32 5, i32 (\d+), i32 [^,]+, i8 (\d), "
                   r"\w+ ([^)]+)\)")
ASSIGN = re.compile(r"^\s*(%[\w.]+) = (.*)$")
CB_LOAD = re.compile(r"@dx\.op\.cbufferLoadLegacy\.\w+\(i32 59, %dx\.types\.Handle (%[\w.]+), "
                     r"i32 (\d+)\)")
HANDLE = re.compile(r"@dx\.op\.createHandle\(i32 57, i8 2, i32 (\d+), ")
CURRENT = set(range(24, 28))
PREVIOUS = set(range(29, 33))


def scene_cb_range(text: str) -> int | None:
    """The range id of the constant buffer at `b8, space0`, from `!dx.resources`."""
    nodes = {int(m.group(1)): m.group(2) for m in re.finditer(r"^!(\d+) = (.*)$", text, re.M)}
    res = re.search(r"^!dx\.resources = !\{!(\d+)\}", text, re.M)
    if not res:
        return None
    lists = re.findall(r"!(\d+)|null", nodes[int(res.group(1))])
    items = nodes[int(res.group(1))][2:-1].split(", ")
    if len(items) < 3 or items[2] == "null":
        return None
    for item in nodes[int(items[2][1:])][2:-1].split(", "):
        e = nodes[int(item[1:])]
        m = re.match(r"!\{i32 (\d+), [^,]+, !\"[^\"]*\", i32 (\d+), i32 (\d+),", e)
        if m and m.group(2) == "0" and m.group(3) == "8":
            return int(m.group(1))
    del lists
    return None


def outputs(text: str) -> dict[int, str]:
    out = {}
    for m in re.finditer(r"^; (\w+)\s+(\d+)\s+(\w+)\s+(\d+)\s+(\w+)\s+(\w+)", text.split(
            "; Output signature:", 1)[1].split(";\n;\n", 1)[0], re.M):
        out[len(out)] = f"{m.group(1)}{m.group(2)}"
    return out


def analyse(text: str) -> list[tuple[str, bool, bool]]:
    scene = scene_cb_range(text)
    body = text[text.index("\ndefine "):]
    body = body[:body.index("\n}\n")]
    defs: dict[str, str] = {}
    last: dict[tuple[int, int], str] = {}
    for line in body.split("\n"):
        code = line.split(";", 1)[0]
        m = ASSIGN.match(code)
        if m:
            defs[m.group(1)] = m.group(2)
        s = STORE.match(code)
        if s:
            last[(int(s.group(1)), int(s.group(2)))] = s.group(3).strip()
    names = outputs(text)
    result = []
    for element in sorted({e for e, _ in last}):
        regs: set[int] = set()
        stack = [v.split()[-1] for (e, _), v in last.items() if e == element]
        seen: set[str] = set()
        while stack:
            v = stack.pop()
            if not v.startswith("%") or v in seen or v not in defs:
                continue
            seen.add(v)
            rhs = defs[v]
            cb = CB_LOAD.search(rhs)
            if cb and scene is not None:
                h = HANDLE.search(defs.get(cb.group(1), ""))
                if h and int(h.group(1)) == scene:
                    regs.add(int(cb.group(2)))
            stack.extend(re.findall(r"%[\w.]+", rhs))
        result.append((names.get(element, f"#{element}"), bool(regs & CURRENT),
                       bool(regs & PREVIOUS)))
    return result


def main() -> int:
    for path in sys.argv[1:]:
        try:
            rows = analyse(open(path, encoding="utf-8").read())
            cells = " ".join(f"{n}:{'C' if c else '-'}{'P' if p else '-'}" for n, c, p in rows)
        except (OSError, ValueError, KeyError, IndexError) as error:
            cells = f"error:{type(error).__name__}"
        print(f"{path}\t{cells}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
