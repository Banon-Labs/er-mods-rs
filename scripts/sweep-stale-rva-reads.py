#!/usr/bin/env python3
"""Route every read of a game global through the build gate, mechanically.

A stale CALL announces itself -- 1.16.2's `0x1405eefb0` is mid-instruction on 1.17 and the
process dies on the spot. A stale READ does not. Every `.data` global moved between the
builds, so `safe_read_usize(base + FOO_RVA)` SUCCEEDS on 1.17 and returns whatever now
occupies the old slot. Two of those were measured: a garbage repository pointer that made
`CreateTpfResCap` divide by zero 894ms into boot, and a stale swapchain root that left a
live process behind a black screen for twenty seconds.

There were 73 such reads, and unlike call sites they need no per-site decision: they are
already fault-tolerant and already have a "this global is not there" branch.
`game_data_addr` returns 0 on refusal, the read fails, and the existing branch runs. So the
rewrite is uniform and this script does it.

It deliberately does NOT touch call sites. Zero is a safe address to fail a read at and a
fatal one to jump to; a `transmute` needs its author to say what refusing means.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# `safe_read_u32(base + FOO_RVA)` / `read_bytes(base + FOO_RVA, ...)`. The `base` expression is
# captured so the rewrite preserves whichever local name the site uses.
READ = re.compile(
    r"\b(safe_read_(?:usize|u64|u32|u16|u8|i32|i64|cstr|bytes)|read_bytes)"
    r"\(\s*(base|module_base|game_base|image_base)\s*\+\s*([A-Z0-9_]*RVA[A-Z0-9_]*)\s*([,)])"
)


def rewrite(text: str, in_game_base: bool) -> tuple[str, int]:
    path = "crate::mem::game_data_addr" if in_game_base else "er_game_base::mem::game_data_addr"

    def sub(m: re.Match) -> str:
        fn, base, const, tail = m.groups()
        return f'{fn}({path}({base}, {const}, "{const}"){tail}'

    return READ.subn(sub, text)


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        failures = []
        got, n = rewrite("let x = unsafe { safe_read_usize(base + GAME_MAN_RVA) }?;", False)
        want = 'let x = unsafe { safe_read_usize(er_game_base::mem::game_data_addr(base, GAME_MAN_RVA, "GAME_MAN_RVA")) }?;'
        if got != want or n != 1:
            failures.append(f"read rewrite produced {got!r}")
        # A call site must be left alone: zero is fatal to jump to.
        call = "let f: Fn = unsafe { core::mem::transmute(base + SOME_RVA) };"
        if rewrite(call, False)[1] != 0:
            failures.append("a transmute call site was rewritten; only reads are safe to zero")
        # An already-rewritten line must not be rewritten again.
        if rewrite(want, False)[1] != 0:
            failures.append("rewriting is not idempotent")
        for line in failures:
            print(f"SELFTEST FAIL: {line}")
        print(f"selftest: {len(failures)} failure(s)")
        return 1 if failures else 0

    total, touched = 0, []
    for path in sorted(repo.glob("crates/**/*.rs")):
        text = path.read_text(encoding="utf-8")
        new, n = rewrite(text, "er-game-base" in path.parts)
        if n:
            total += n
            touched.append((path.relative_to(repo), n))
            if args.apply:
                path.write_text(new, encoding="utf-8")
    for rel, n in touched:
        print(f"  {n:3d}  {rel}")
    verb = "rewrote" if args.apply else "would rewrite"
    print(f"{verb} {total} read site(s) across {len(touched)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
