#!/usr/bin/env python3
"""Reader for the `rego_compile_check` cupcake signal: does this write leave the policies compiling?

What happened, 2026-11-11: a subagent added `.cupcake/policies/claude/no_stop_waiting_agent.rego` and
then, in a second Edit, made line 40 call `value_of`, a function the next Edit was going to define.
Cupcake compiles every policy before it evaluates any, so from that Edit on it failed to start:
"OPA compilation failed ... rego_type_error: undefined function value_of". Every tool call in every
session on this checkout was refused, the Edit that would have defined the function included, and
the user had to delete the file by hand.

This reader answers, before the write happens, whether the policy set the engine loads would still
compile with it. It builds the file's new content -- Write's `content`; for Edit and MultiEdit the
current file with each `old_string` replaced by its `new_string`; for a Bash heredoc into a `.rego`
(`cat > f.rego <<'EOF'`, `cat <<'EOF' > f.rego`, `tee f.rego <<'EOF'`) its body; for `cp`/`mv`/
`install` onto a `.rego` the source file -- and judges it against a copy of the `.cupcake` directory
the file lives in (`policies/`, `system/`, `signals/`, `rulebook.yml`), with that content in place.

The package set is the engine's own. The copy is handed to `cupcake eval` with this script as its
`--opa-path`. Measured 2026-11-11 against cupcake 0.5.2 by logging what it hands opa: it scans
`.cupcake/policies/claude/` recursively, keeps only the builtins the rulebook enables (their source is
embedded in cupcake, not on disk), adds `.cupcake/system/*.rego`, flattens the lot into a temp
directory and runs `opa build -t wasm -O 2 -e cupcake/system/evaluate <dir>`. In shim mode this script
receives that call and runs `opa check <dir>` on the directory cupcake prepared instead, so the check
sees exactly the files the engine would compile. `opa check` reports what broke the engine -- the
`value_of` replay prints the engine's own line, "rego_type_error: undefined function value_of" -- and
type and parse errors the same way. The global set (`--global-config`) is pointed at an empty
directory, because a project file cannot change it.

A file outside `policies/` and `system/` (`tests/`, `signals/`) is not scanned by the engine and is
not judged. A Bash write this cannot resolve to content (`sed -i`, a redirect from another command,
a python program) is not judged either.

Prints, tab-separated, a first line and opa's output under it:

    REGOCHECK  error      <path>   then opa's errors   the write breaks compilation
    REGOCHECK  unchecked  <path>   then the reason     opa or cupcake missing, a timeout, a crash

and nothing when the write compiles or is not judged. A failure that the set already had before
this write, and whose errors do not name this file, is not this write's doing and prints nothing.

Fails open: anything that stops the check from running allows the write. Cupcake 0.5.2 has no
channel for a note on an allowed PreToolUse call (measured: an `add_context` decision there comes
back as `{}`), so `unchecked` reaches only stderr and this signal's output.

`--selftest` runs the Bash write parser over its own corpus.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ENTRYPOINT = "cupcake/system/evaluate"
RESULT_ENV = "ER_REGO_CHECK_RESULT"
REAL_OPA_ENV = "ER_REGO_CHECK_OPA"
# The signal's own budget: cupcake drops a discovered signal after 5 s, which is an allow. Each
# check is one cupcake start plus one `opa check`, about 0.5 s on this tree; a failing write may need
# a second one against the unchanged set.
DEADLINE_SECONDS = 4.2
COPIED = ("policies", "system", "signals", "rulebook.yml")
JUDGED_TOP = ("policies", "system")


# --- shim mode: cupcake's `--opa-path` ------------------------------------------------------------

def shim(argv: list[str]) -> int:
    """Stand in for `opa` under cupcake. The project compile becomes `opa check` on the directory
    cupcake prepared, recorded to $ER_REGO_CHECK_RESULT, and then exits 1 so cupcake stops there;
    anything else goes to the real opa."""
    real = os.environ.get(REAL_OPA_ENV) or shutil.which("opa") or "opa"
    result = os.environ.get(RESULT_ENV)
    if argv[:1] != ["build"] or PROJECT_ENTRYPOINT not in argv or not result:
        os.execv(real, [real, *argv])
    out = argv[argv.index("-o") + 1] if "-o" in argv[:-1] else ""
    dirs = [a for a in argv[1:] if os.path.isdir(a) and a != out]
    if not dirs:
        return 2
    proc = subprocess.run([real, "check", dirs[-1]], capture_output=True, text=True, timeout=10)
    Path(result).write_text(json.dumps({
        "rc": proc.returncode, "output": (proc.stdout + proc.stderr).strip(), "dir": dirs[-1],
    }), encoding="utf-8")
    return 1


# --- what the write would leave in the file -------------------------------------------------------

def cupcake_root(path: str) -> tuple[str, str] | None:
    """(the `.cupcake` directory, the path inside it) for a path under one, else None."""
    parts = Path(path).parts
    if ".cupcake" not in parts:
        return None
    i = len(parts) - 1 - parts[::-1].index(".cupcake")
    return str(Path(*parts[: i + 1])), str(Path(*parts[i + 1:])) if i + 1 < len(parts) else ""


def read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def apply_edits(current: str, edits: list[dict]) -> str | None:
    """The content after each edit in turn, or None when an edit would not apply (the tool then
    fails on its own and writes nothing)."""
    text = current
    for edit in edits:
        old, new = edit.get("old_string"), edit.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or old == "" or old not in text:
            return None
        text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
    return text


HEREDOC_RE = re.compile(
    r"(?P<cmd>\bcat\b|\btee\b)(?P<args>[^\n<]*?)<<-?\s*(?P<q>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P=q)(?P<rest>[^\n]*)\n(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*(?:\n|$)",
    re.DOTALL,
)
REDIRECT_RE = re.compile(r"(?P<op>>>?)\s*(?P<path>'[^']+'|\"[^\"]+\"|[^\s;&|<>]+)")
COPY_RE = re.compile(
    r"(?:^|[;&|]\s*|\s)(?:cp|mv|install)(?:\s+-[A-Za-z-]+)*\s+(?P<src>'[^']+'|\"[^\"]+\"|[^\s;&|<>]+)"
    r"\s+(?P<dst>'[^']+\.rego'|\"[^\"]+\.rego\"|[^\s;&|<>]+\.rego)(?=\s|$|[;&|])"
)
CD_RE = re.compile(r"\A\s*cd\s+(?P<dir>'[^']+'|\"[^\"]+\"|[^\s;&|]+)\s*&&")


def unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    return token


def bash_writes(command: str, cwd: str) -> list[tuple[str, str]]:
    """(path, new content) for every `.rego` write in a Bash command that this can resolve."""
    cd = CD_RE.match(command)
    if cd:
        cwd = os.path.join(cwd, os.path.expanduser(unquote(cd.group("dir"))))

    def resolve(token: str) -> str:
        return os.path.normpath(os.path.join(cwd, os.path.expanduser(unquote(token))))

    found: list[tuple[str, str]] = []
    for m in HEREDOC_RE.finditer(command):
        body = m.group("body") + "\n"
        targets: list[tuple[str, bool]] = []
        if m.group("cmd") == "cat":
            for r in REDIRECT_RE.finditer(m.group("args") + " " + m.group("rest")):
                targets.append((resolve(r.group("path")), r.group("op") == ">>"))
        else:
            args = m.group("args").split()
            append = "-a" in args or "--append" in args
            targets += [(resolve(a), append) for a in args if not a.startswith("-")]
        for path, append in targets:
            if path.endswith(".rego"):
                found.append((path, (read(path) or "") + body if append else body))
    for m in COPY_RE.finditer(command):
        content = read(resolve(m.group("src")))
        if content is not None:
            found.append((resolve(m.group("dst")), content))
    return found


def proposed_writes(event: dict) -> list[tuple[str, str]]:
    tool = event.get("tool_name")
    inp = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else os.getcwd()
    path = inp.get("file_path")
    if tool == "Write" and isinstance(path, str) and isinstance(inp.get("content"), str):
        return [(os.path.normpath(os.path.join(cwd, path)), inp["content"])]
    if tool in ("Edit", "MultiEdit") and isinstance(path, str):
        full = os.path.normpath(os.path.join(cwd, path))
        current = read(full)
        if current is None:
            return []
        edits = inp.get("edits") if tool == "MultiEdit" else [inp]
        new = apply_edits(current, edits if isinstance(edits, list) else [])
        return [(full, new)] if new is not None else []
    if tool == "Bash" and isinstance(inp.get("command"), str):
        return bash_writes(inp["command"], cwd)
    return []


# --- the check ------------------------------------------------------------------------------------

class Unchecked(Exception):
    pass


def compile_set(root: str, writes: dict[str, str], deadline: float) -> tuple[int, str, str]:
    """(opa check's exit code, its output, the temp directory it named) for the engine's set built
    from `root` with `writes` (paths inside the `.cupcake` directory) in place."""
    cupcake = os.environ.get("CUPCAKE_BIN") or shutil.which("cupcake")
    opa = shutil.which("opa")
    if not cupcake or not opa:
        raise Unchecked("cupcake or opa is not on PATH")
    with tempfile.TemporaryDirectory(prefix="rego-compile-check-") as tmp:
        copy = Path(tmp) / "project" / ".cupcake"
        copy.mkdir(parents=True)
        for name in COPIED:
            src = Path(root) / name
            if src.is_dir():
                shutil.copytree(src, copy / name, ignore=shutil.ignore_patterns("state"))
            elif src.is_file():
                shutil.copy2(src, copy / name)
        for rel, content in writes.items():
            dst = copy / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content, encoding="utf-8")
        empty_global = Path(tmp) / "no-global"
        empty_global.mkdir()
        result = Path(tmp) / "result.json"
        env = {**os.environ, RESULT_ENV: str(result), REAL_OPA_ENV: opa}
        event = {"session_id": "rego-compile-check", "transcript_path": "", "cwd": str(copy.parent),
                 "hook_event_name": "PreToolUse", "tool_name": "RegoCompileCheck", "tool_input": {}}
        remaining = deadline - time.monotonic()
        if remaining <= 0.2:
            raise Unchecked("no time left for the check inside cupcake's 5 s signal budget")
        # Popen, because the cap is what is left of the signal's budget rather than a constant.
        proc = subprocess.Popen(
            [cupcake, "eval", "--harness", "claude", "--log-level", "error",
             "--opa-path", str(Path(__file__).resolve()), "--policy-dir", str(copy),
             "--global-config", str(empty_global)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env, cwd=str(copy.parent),
        )
        try:
            proc.communicate(json.dumps(event), timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise Unchecked("the check timed out")
        try:
            data = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Unchecked("cupcake never reached the project compile")
        return int(data.get("rc", 1)), str(data.get("output") or ""), str(data.get("dir") or "")


def judge(event: dict) -> str:
    writes = proposed_writes(event)
    by_root: dict[str, dict[str, str]] = {}
    for path, content in writes:
        if not path.endswith(".rego"):
            continue
        found = cupcake_root(path)
        if found is None:
            continue
        root, rel = found
        if rel.split(os.sep, 1)[0] not in JUDGED_TOP:
            continue  # the engine scans only policies/ and system/
        by_root.setdefault(root, {})[rel] = content
    deadline = time.monotonic() + DEADLINE_SECONDS
    for root, rels in by_root.items():
        shown = os.path.join(root, sorted(rels)[0])
        try:
            rc, output, tmpdir = compile_set(root, rels, deadline)
            if rc == 0:
                continue
            names = {os.path.basename(r) for r in rels}
            if not any(n in output for n in names):
                base_rc, _out, _dir = compile_set(root, {}, deadline)
                if base_rc != 0:
                    continue  # already failing without this write, and not in this file
        except Unchecked as exc:
            sys.stderr.write(f"rego_compile_check: not checked ({exc}); allowing {shown}\n")
            return f"REGOCHECK\tunchecked\t{shown}\n{exc}"
        if tmpdir:
            output = output.replace(tmpdir.rstrip("/") + "/", "<engine set>/")
        return f"REGOCHECK\terror\t{shown}\n{output}"
    return ""


# --- selftest -------------------------------------------------------------------------------------

def selftest() -> int:
    cwd = "/r"
    cases = [
        ("cat > .cupcake/policies/claude/a.rego <<'EOF'\npackage a\nx := 1\nEOF\n",
         [("/r/.cupcake/policies/claude/a.rego", "package a\nx := 1\n")]),
        ("cat <<EOF > /abs/.cupcake/system/b.rego\npackage b\nEOF",
         [("/abs/.cupcake/system/b.rego", "package b\n")]),
        ("tee .cupcake/policies/claude/c.rego <<'X' >/dev/null\npackage c\nX\n",
         [("/r/.cupcake/policies/claude/c.rego", "package c\n")]),
        ("cd /w && cat > .cupcake/policies/claude/d.rego <<'EOF'\npackage d\nEOF",
         [("/w/.cupcake/policies/claude/d.rego", "package d\n")]),
        ("cat > notes.md <<'EOF'\nx\nEOF", []),
        ("opa test .cupcake/policies/claude/a.rego .cupcake/tests/a_test.rego", []),
        ("sed -i 's/a/b/' .cupcake/policies/claude/a.rego", []),
    ]
    bad = 0
    for command, want in cases:
        got = [(p, c) for p, c in bash_writes(command, cwd)]
        if got != want:
            bad += 1
            print(f"FAIL {command!r}: got {got!r}, want {want!r}", file=sys.stderr)
    roots = [
        ("/r/.cupcake/policies/claude/a.rego", ("/r/.cupcake", "policies/claude/a.rego")),
        ("/r/.worktrees/w/.cupcake/system/e.rego", ("/r/.worktrees/w/.cupcake", "system/e.rego")),
        ("/r/scripts/a.rego", None),
    ]
    for path, want in roots:
        if cupcake_root(path) != want:
            bad += 1
            print(f"FAIL cupcake_root({path!r}) = {cupcake_root(path)!r}, want {want!r}", file=sys.stderr)
    edit = apply_edits("a b a", [{"old_string": "a", "new_string": "c"}])
    if edit != "c b a":
        bad += 1
        print(f"FAIL apply_edits first-only: {edit!r}", file=sys.stderr)
    if apply_edits("a b a", [{"old_string": "a", "new_string": "c", "replace_all": True}]) != "c b c":
        bad += 1
        print("FAIL apply_edits replace_all", file=sys.stderr)
    if apply_edits("a", [{"old_string": "z", "new_string": "c"}]) is not None:
        bad += 1
        print("FAIL apply_edits missing anchor", file=sys.stderr)
    print("cupcake_rego_compile_check selftest:", "OK" if not bad else f"{bad} failure(s)")
    return 1 if bad else 0


def main() -> int:
    argv = sys.argv[1:]
    if argv == ["--selftest"]:
        return selftest()
    if argv and argv[0] != "--signal":
        return shim(argv)
    raw = sys.stdin.read()
    try:
        event = json.loads(raw or "{}")
    except ValueError:
        return 0
    if not isinstance(event, dict) or event.get("hook_event_name") != "PreToolUse":
        return 0
    try:
        line = judge(event)
    except Exception as exc:  # noqa: BLE001 -- fail open, see the module docstring
        sys.stderr.write(f"rego_compile_check: not checked ({exc!r})\n")
        return 0
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
