#!/usr/bin/env python3
"""Refuse Lua the game's embedded AI interpreter cannot load.

Elden Ring's AI Lua is a Lua 5.1 dialect whose parser has no `#` length operator. Measured
2026-10-06: `while #fits > 0` in `scripts/frida/ai-lua/mods/brain_turtles.lua` made the AI lab's
hot reload fail 510 times with "unexpected symbol near `#'", and the turtles silently fell back to
stock AI. `luac5.1 -p` accepted that file, so a plain syntax check cannot catch this.

Two checks over every `.lua` under `scripts/frida/ai-lua/`:

  1. the dialect check: no `#` and no `%` outside comments and strings. Those are the constructs
     with evidence of rejection in `~/.cache/er-frida/ai-lab.jsonl` `reload-failed` records: `#`
     510 times, then `n % 4 + 1` on 2026-10-06 ("unexpected symbol near `%'"). Add a construct
     here only with a load failure that shows it.
  2. `luac5.1 -p` for plain syntax errors. Where `luac5.1` is not installed (a CI runner) that
     half prints that it was skipped and the dialect half still runs.

    python3 scripts/check-ai-lua-dialect.py             # the gate
    python3 scripts/check-ai-lua-dialect.py --selftest  # proves the scanner catches and ignores
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LUA_ROOT = REPO / "scripts/frida/ai-lua"
LUAC = "luac5.1"

_LONG_OPEN = re.compile(r"\[(=*)\[")


def dialect_violations(src: str) -> list[tuple[int, int, str]]:
    """(line, column, message) for each construct the game's parser rejects.

    A small lexer: it walks comments (`--` and `--[==[ ]==]`), quoted strings with escapes, and
    long strings (`[==[ ]==]`), and reports a `#` found anywhere else.
    """
    out: list[tuple[int, int, str]] = []
    i, n, line, line_start = 0, len(src), 1, 0

    def skip_long(pos: int, level: str) -> int:
        nonlocal line, line_start
        close = "]" + level + "]"
        end = src.find(close, pos)
        end = n if end < 0 else end + len(close)
        for k in range(pos, end):
            if src[k] == "\n":
                line, line_start = line + 1, k + 1
        return end

    while i < n:
        c = src[i]
        if c == "\n":
            line, line_start = line + 1, i + 1
            i += 1
        elif src.startswith("--", i):
            m = _LONG_OPEN.match(src, i + 2)
            if m:
                i = skip_long(m.end(), m.group(1))
            else:
                nl = src.find("\n", i)
                i = n if nl < 0 else nl
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            i = j + 1
        elif c == "[" and _LONG_OPEN.match(src, i):
            m = _LONG_OPEN.match(src, i)
            i = skip_long(m.end(), m.group(1))
        elif c == "#":
            out.append(
                (
                    line,
                    i - line_start + 1,
                    "`#` length operator: the game's AI Lua rejects it "
                    "(\"unexpected symbol near `#'\"); count with a loop or keep a counter",
                )
            )
            i += 1
        elif c == "%":
            out.append(
                (
                    line,
                    i - line_start + 1,
                    "`%` modulo operator: the game's AI Lua rejects it "
                    "(\"unexpected symbol near `%'\"); wrap the value by hand",
                )
            )
            i += 1
        else:
            i += 1
    return out


def luac_errors(path: Path) -> str | None:
    proc = subprocess.run(
        [LUAC, "-p", str(path)], capture_output=True, text=True, timeout=25
    )
    return None if proc.returncode == 0 else (proc.stderr or proc.stdout).strip()


def check(root: Path) -> int:
    files = sorted(root.rglob("*.lua"))
    if not files:
        print(f"check-ai-lua-dialect: no .lua files under {root}", file=sys.stderr)
        return 1
    have_luac = shutil.which(LUAC) is not None
    failures = 0
    for path in files:
        rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
        for ln, col, msg in dialect_violations(path.read_text(encoding="utf-8")):
            print(f"{rel}:{ln}:{col}: {msg}")
            failures += 1
        if have_luac:
            err = luac_errors(path)
            if err:
                print(f"{rel}: {LUAC} -p: {err}")
                failures += 1
    if not have_luac:
        print(f"check-ai-lua-dialect: {LUAC} not installed; syntax half SKIPPED, dialect half ran")
    if failures:
        print(f"check-ai-lua-dialect: {failures} problem(s) in {len(files)} file(s)")
        return 1
    print(f"check-ai-lua-dialect: ok ({len(files)} file(s))")
    return 0


def selftest() -> int:
    flagged = {
        "while #fits > 0 do end": [(1, 7)],
        "local a = 1\nlocal n = #t": [(2, 11)],
        "x = '#' .. #y": [(1, 12)],
        "s = [[\n#]] n = #z": [(2, 9)],
        "n = i % 4 + 1": [(1, 7)],
    }
    clean = [
        "-- while #fits > 0",
        "--[[ #a\n #b ]] x = 1",
        "--[==[ ]] #a ]==] x = 1",
        "s = '#'",
        's = "a\\"#"',
        "s = [[ # ]]",
        "s = [=[ ]] # ]=]",
        "t = a[b[1]]",
    ]
    bad = 0
    for src, want in flagged.items():
        got = [(ln, col) for ln, col, _ in dialect_violations(src)]
        if got != want:
            print(f"selftest FAIL: {src!r} -> {got}, want {want}")
            bad += 1
    for src in clean:
        got = dialect_violations(src)
        if got:
            print(f"selftest FAIL: {src!r} flagged {[(a, b) for a, b, _ in got]}, want none")
            bad += 1
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "ok.lua").write_text("local t = {}\n-- #t\n", encoding="utf-8")
        if check(root) != 0:
            print("selftest FAIL: clean tree reported a problem")
            bad += 1
        (root / "len.lua").write_text("local t = {}\nwhile #t > 0 do end\n", encoding="utf-8")
        if check(root) != 1:
            print("selftest FAIL: tree with `#t` passed")
            bad += 1
        (root / "len.lua").unlink()
        if shutil.which(LUAC):
            (root / "syntax.lua").write_text("local = 1\n", encoding="utf-8")
            if check(root) != 1:
                print(f"selftest FAIL: {LUAC} syntax error passed")
                bad += 1
        else:
            print(f"selftest: {LUAC} not installed; syntax-error case not exercised")
    print(f"selftest: {bad} failure(s)")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    if argv[1:]:
        print("usage: check-ai-lua-dialect.py [--selftest]", file=sys.stderr)
        return 2
    return check(LUA_ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
