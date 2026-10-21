"""Print the backward slice of a vertex shader's `SV_Position` stores, in program order.

Research prototype for the shader-editor vertex question: the readable companion of
`pos_slice.py`, for looking at why two position expressions hash differently.

Usage: slice_print.py <shader.ll>   (text from `dxc -dumpbin`)
"""
import re
import sys


def main() -> int:
    text = open(sys.argv[1], encoding="utf-8").read()
    body = text[text.index("\ndefine "):]
    body = body[:body.index("\n}\n")]
    lines = [line.split(";", 1)[0].rstrip() for line in body.split("\n")]
    defs = {}
    for index, code in enumerate(lines):
        m = re.match(r"^\s*(%[\w.]+) = (.*)$", code)
        if m:
            defs[m.group(1)] = index
    sig = text.split("; Output signature:", 1)[1].split(";\n;\n", 1)[0]
    names = re.findall(r"^; (\w+)\s+(\d+)\s+(\w+)\s+(\d+)\s+(\w+)\s+(\w+)", sig, re.M)
    pos_ids = [i for i, n in enumerate(names) if n[0] == "SV_Position"]
    want: set[int] = set()
    stack: list[str] = []
    for index, code in enumerate(lines):
        s = re.match(r"^\s*call void @dx\.op\.storeOutput\.f32\(i32 5, i32 (\d+),", code)
        if s and int(s.group(1)) in pos_ids:
            want.add(index)
            stack += re.findall(r"%[\w.]+", code)
    while stack:
        value = stack.pop()
        if value in defs and defs[value] not in want:
            want.add(defs[value])
            stack += re.findall(r"%[\w.]+", lines[defs[value]].split("=", 1)[1])
    for index in sorted(want):
        print(lines[index].strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
