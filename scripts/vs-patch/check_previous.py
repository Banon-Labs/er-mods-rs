"""Structural check of a `patch_vs_ll.py --previous` result against the plain wobble patch.

Research prototype for the shader-editor vertex question. Prints one word per check:

- `cur=same` when `SV_Position` hashes the same in both (pos_slice.py), so the previous-time
  edit left the current position alone;
- `prev=ok` when exactly one output reads the previous time (`%er_ptime`) and its slice does not
  read the current time (`%er_time`);
- `others=same` when every other output reads the same editor times as in the plain patch.

Usage: check_previous.py <wobble.ll> <wprev.ll>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pos_slice  # noqa: E402

STORE = re.compile(r"^\s*call void @dx\.op\.storeOutput\.\w+\(i32 5, i32 (\d+), i32 [^,]+, i8 \d, "
                   r"\w+ ([^)]+)\)")
ASSIGN = re.compile(r"^\s*(%[\w.]+) = (.*)$")
TIMES = ("%er_time", "%er_ptime")


def times_per_output(text: str) -> dict[int, frozenset[str]]:
    body = text[text.index("\ndefine "):]
    body = body[:body.index("\n}\n")]
    defs: dict[str, str] = {}
    stores: dict[int, list[str]] = {}
    for line in body.split("\n"):
        code = line.split(";", 1)[0]
        m = ASSIGN.match(code)
        if m:
            defs[m.group(1)] = m.group(2)
        s = STORE.match(code)
        if s:
            stores.setdefault(int(s.group(1)), []).append(s.group(2).strip().split()[-1])
    out = {}
    for element, values in stores.items():
        seen: set[str] = set()
        stack = list(values)
        while stack:
            v = stack.pop()
            if v in seen or v not in defs:
                continue
            seen.add(v)
            stack.extend(re.findall(r"%[\w.]+", defs[v]))
        out[element] = frozenset(t for t in TIMES if t in seen)
    return out


def main() -> int:
    plain = open(sys.argv[1], encoding="utf-8").read()
    prev = open(sys.argv[2], encoding="utf-8").read()
    cur_same = pos_slice.build(plain) == pos_slice.build(prev)
    a, b = times_per_output(plain), times_per_output(prev)
    readers = [e for e, t in b.items() if "%er_ptime" in t]
    prev_ok = len(readers) == 1 and b[readers[0]] == frozenset({"%er_ptime"})
    others = all(a.get(e) == t for e, t in b.items() if e not in readers)
    print(f"cur={'same' if cur_same else 'differs'} prev={'ok' if prev_ok else 'bad'} "
          f"others={'same' if others else 'differ'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
