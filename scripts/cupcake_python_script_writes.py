#!/usr/bin/env python3
"""Which uncommitted python script files named by a pending Bash command only read?

Read by `.cupcake/signals/python_script_writes.sh`, which feeds it the pending hook event on
stdin, and through that by `.cupcake/policies/claude/bash_no_python_file_write.rego`.

Why this exists: that guard refuses `python3 <path>.py` for any path outside this repo's
committed `scripts/` tree, because the write it exists to stop can hide inside a scratch file
(2026-09-17: a patch script written to the scratchpad with a heredoc, then run as a separate
call). The command line alone cannot tell that file from one that only reads, so the guard
refused both -- measured 2026-09-29, `timeout 29 python3 <scratchpad>/analyse.py <scratchpad>`
was denied although the script did nothing but `json.load(open(path))` and print. This reads
the file the command names and says which of the two it is, so the guard can tell them apart.

Output, one line per python script operand found in the command:

    `READONLY <operand>`        the file exists and nothing in it writes, as far as the scan sees
    `WRITES <operand> <why>`    a construct that writes, or could write, was found
    `UNKNOWN <operand> <why>`   the file could not be pinned down or read

`<operand>` is the word exactly as the shell would hand it to python before expansion, with any
surrounding quotes removed, so the policy can match it against its own tokenisation. The policy
lifts its refusal only for an operand on a `READONLY` line; everything else, including no output
at all, leaves the refusal in place.

This is a habit guard, not a sandbox. It is meant to catch an agent editing files with a python
program, which is written plainly (`open(p, 'w')`, `.write_text(`, `shutil.copy(`). A program
built to evade a scan (`getattr(__builtins__, x)` with a computed name, a C extension) is out of
scope; the scan still fails closed on the obvious forms of that -- `exec`, `eval`, `__import__`,
non-literal `getattr`, a non-literal `open` mode, `importlib` pointed anywhere but this repo's
`scripts/` tree -- so evasion takes intent rather than accident.

`importlib` gets one narrow allowance, because loading a committed tool as a library is how a
scratch analysis reuses it (the 2026-09-29 script loaded `scripts/er-mechanics-ashes.py` with
`spec_from_file_location`): the location must be a string literal naming a file under this
repo's `scripts/` tree by the same path test the policy applies to `python3 <path>.py`, and a
`sys.path` entry must be a literal naming a directory there. Anything else from `importlib`, or
a computed location, counts as a write.

Fail-closed choices on the command, each of which makes every operand `UNKNOWN`:

* a command substitution or backtick (the text run is not the text read here);
* a write-capable tool anywhere in the command (`cp`, `mv`, `tee`, `sed`, ...), since it could
  replace the script between this read and python's;
* the script path occurring anywhere else in the command, e.g. as a redirect target;
* a `$NAME` that is not assigned exactly once, to a literal, earlier in the same command;
* a path that is relative after expansion (the working directory is not known here).
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import sys
import tempfile

MAX_SCRIPT_BYTES = 1 << 20

# Tools that can put different bytes at the script path inside the same command.
WRITE_TOOLS = {
    "cp", "mv", "ln", "install", "rsync", "tee", "dd", "sed", "perl", "patch", "truncate",
    "curl", "wget", "tar", "unzip", "git", "scp", "ruby", "node",
}

# Standard modules whose purpose is running other code or mutating the filesystem.
DENIED_MODULES = {
    "subprocess", "shutil", "tempfile", "sqlite3", "ctypes", "pty",
    "multiprocessing", "dbm", "shelve", "mmap", "zipfile", "tarfile", "fileinput", "runpy",
    "code", "codeop", "cffi", "sh", "plumbum",
}

# `os.<name>` calls that write, delete, or run something else.
OS_MUTATORS = {
    "remove", "unlink", "rename", "renames", "replace", "rmdir", "removedirs", "mkdir",
    "makedirs", "symlink", "link", "chmod", "chown", "lchown", "truncate", "ftruncate", "utime",
    "mkfifo", "mknod", "open", "write", "pwrite", "writev", "system", "popen", "startfile",
    "fork", "forkpty", "posix_spawn", "posix_spawnp", "execl", "execle", "execlp", "execlpe",
    "execv", "execve", "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe", "kill", "putenv",
}

# Method names that write whatever receiver they are called on. Only names with no common
# non-filesystem meaning belong here: `remove`, `replace` and `rename` are list, str and
# dataframe methods and are caught through `os.` instead.
WRITE_METHODS = {
    "write_text", "write_bytes", "touch", "unlink", "symlink_to", "hardlink_to", "mkdir",
    "rmdir", "chmod", "lchmod", "savefig", "savetxt", "savez", "savez_compressed", "to_csv",
    "to_parquet", "to_pickle", "to_excel", "to_feather", "to_hdf", "to_sql", "imwrite",
    "copyfile", "copytree", "rmtree", "move",
}

DYNAMIC_BUILTINS = {"exec", "eval", "compile", "__import__", "breakpoint"}

MODE_RE = re.compile(r"^[rwxabtU+]{1,4}$")
ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
VAR_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "\n", "|&", ";;"}


def python_word(word: str) -> bool:
    base = word.rsplit("/", 1)[-1]
    return base.startswith("python")


def tokenize(command: str) -> list[str] | None:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def script_operands(tokens: list[str]) -> list[int]:
    """Indexes of each `.py` word handed to a python interpreter as its script."""
    found = []
    for index, word in enumerate(tokens):
        if not python_word(word):
            continue
        for j in range(index + 1, len(tokens)):
            nxt = tokens[j]
            if nxt in SEPARATORS or set(nxt) <= set("<>&|;()"):
                break
            if nxt.startswith("-"):
                continue
            if nxt.endswith(".py"):
                found.append(j)
            break
    return found


def assignments(tokens: list[str]) -> dict[str, str | None]:
    """`NAME=value` words in the command. A name assigned twice, or to a non-literal, maps to None."""
    seen: dict[str, str | None] = {}
    for word in tokens:
        match = ASSIGN_RE.match(word)
        if not match:
            continue
        name, value = match.group(1), match.group(2)
        literal = "$" not in value and "`" not in value
        seen[name] = None if name in seen or not literal else value
    return seen


def expand(word: str, env: dict[str, str | None]) -> str | None:
    failed = False

    def sub(match: re.Match[str]) -> str:
        nonlocal failed
        value = env.get(match.group(1) or match.group(2))
        if value is None:
            failed = True
            return ""
        return value

    out = VAR_RE.sub(sub, word)
    if failed or "$" in out:
        return None
    if out.startswith("~"):
        out = os.path.expanduser(out)
    return out


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = dotted(node.value)
        return f"{head}.{node.attr}" if head else node.attr
    return ""


def str_const(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def mode_writes(mode: str) -> bool:
    return any(ch in mode for ch in "wxa+")


def open_call_verdict(call: ast.Call, builtin: bool) -> str | None:
    for kw in call.keywords:
        if kw.arg is None:
            return "open() with **kwargs"
        if kw.arg == "mode":
            mode = str_const(kw.value)
            if mode is None:
                return "open() with a non-literal mode"
            if mode_writes(mode):
                return f"open() mode {mode!r}"
    for position, arg in enumerate(call.args):
        if isinstance(arg, ast.Starred):
            return "open() with *args"
        value = str_const(arg)
        if value is not None and MODE_RE.match(value) and mode_writes(value):
            return f"open() mode {value!r}"
        if builtin and position == 1 and value is None:
            return "open() with a non-literal mode"
    return None


def committed_script_path(path: str) -> bool:
    """The policy's `committed_script_path` for an absolute path: `.../er-mods-rs/.../scripts/<x>`."""
    if not path.startswith("/") or ".." in path:
        return False
    segments = path.split("/")
    for repo_index, segment in enumerate(segments):
        if segment != "er-mods-rs":
            continue
        for scripts_index in range(repo_index + 1, len(segments) - 1):
            if segments[scripts_index] == "scripts":
                return True
    return False


def committed_scripts_dir(path: str) -> bool:
    return committed_script_path(path.rstrip("/") + "/x.py")


# `importlib` calls that only look a module up or run a loader already vetted by the rules below.
IMPORTLIB_PASSIVE = {"module_from_spec", "exec_module", "find_spec", "reload", "invalidate_caches"}


def local_module(script_dir: str, top: str) -> bool:
    return os.path.exists(os.path.join(script_dir, top + ".py")) or os.path.isdir(
        os.path.join(script_dir, top)
    )


def importlib_verdict(
    call: ast.Call, func: ast.Attribute, name: str, script_dir: str
) -> str | None:
    if func.attr in {"insert", "append", "extend"} and name.startswith("sys.path."):
        target = call.args[-1] if call.args else None
        literal = str_const(target) if target is not None else None
        if literal is None or not committed_scripts_dir(literal):
            return "extends sys.path outside this repo's scripts/"
        return None
    if not name.startswith("importlib.") and func.attr not in {
        "spec_from_file_location", "SourceFileLoader", "SourcelessFileLoader",
        "ExtensionFileLoader", "spec_from_loader", "import_module",
    }:
        return None
    if func.attr in {"spec_from_file_location", "SourceFileLoader"}:
        location = call.args[1] if len(call.args) > 1 else None
        for kw in call.keywords:
            if kw.arg in {"location", "path"}:
                location = kw.value
        literal = str_const(location) if location is not None else None
        if literal is None or not committed_script_path(literal):
            return f"importlib.{func.attr} outside this repo's scripts/"
        return None
    if func.attr == "import_module":
        literal = str_const(call.args[0]) if call.args else None
        if literal is None:
            return "importlib.import_module with a non-literal name"
        top = literal.split(".")[0]
        if top in DENIED_MODULES:
            return f"imports {top}"
        if local_module(script_dir, top):
            return f"imports local module {top}"
        return None
    if func.attr in IMPORTLIB_PASSIVE:
        return None
    return f"calls {name}()"


def scan_source(source: str, script_dir: str) -> str | None:
    """None when nothing in `source` writes; otherwise the first reason found."""
    try:
        tree = ast.parse(source)
    except SyntaxError as err:
        return f"does not parse ({err.msg})"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.level:
                return "relative import"
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                top = name.split(".")[0]
                if top in DENIED_MODULES:
                    return f"imports {top}"
                if local_module(script_dir, top):
                    return f"imports local module {top}"
            if isinstance(node, ast.ImportFrom) and node.module in {"os", "pathlib", "io"}:
                for alias in node.names:
                    if alias.name in OS_MUTATORS or alias.name in WRITE_METHODS:
                        return f"imports {node.module}.{alias.name}"
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = dotted(func)
        if isinstance(func, ast.Name):
            if func.id in DYNAMIC_BUILTINS:
                return f"calls {func.id}()"
            if func.id == "open":
                reason = open_call_verdict(node, builtin=True)
                if reason:
                    return reason
            if func.id in {"getattr", "setattr", "delattr"}:
                attr = str_const(node.args[1]) if len(node.args) > 1 else None
                if attr is None:
                    return f"{func.id}() with a non-literal name"
                if attr in WRITE_METHODS or attr in OS_MUTATORS or attr in DYNAMIC_BUILTINS:
                    return f"{func.id}(..., {attr!r})"
        elif isinstance(func, ast.Attribute):
            reason = importlib_verdict(node, func, name, script_dir)
            if reason:
                return reason
            if name.startswith("os.") and func.attr in OS_MUTATORS:
                return f"calls {name}()"
            if func.attr in WRITE_METHODS:
                return f"calls .{func.attr}()"
            if func.attr == "open":
                reason = open_call_verdict(node, builtin=name in {"io.open", "codecs.open"})
                if reason:
                    return reason
    return None


def judge(command: str) -> list[str]:
    if "python" not in command or ".py" not in command:
        return []
    tokens = tokenize(command)
    if tokens is None:
        return []
    operands = script_operands(tokens)
    if not operands:
        return []
    command_reason = None
    if "`" in command or "$(" in command or "<(" in command or ">(" in command:
        command_reason = "command substitution"
    elif any(t.rsplit("/", 1)[-1] in WRITE_TOOLS for t in tokens):
        command_reason = "a write-capable tool in the same command"
    env = assignments(tokens)
    lines = []
    for index in operands:
        raw = tokens[index]
        if command_reason:
            lines.append(f"UNKNOWN {raw} {command_reason}")
            continue
        path = expand(raw, env)
        if path is None:
            lines.append(f"UNKNOWN {raw} unresolved variable")
            continue
        if not os.path.isabs(path):
            lines.append(f"UNKNOWN {raw} relative path")
            continue
        path = os.path.normpath(path)
        others = [expand(t, env) for i, t in enumerate(tokens) if i != index]
        if any(t == raw for i, t in enumerate(tokens) if i != index) or any(
            o is not None and os.path.normpath(o) == path for o in others if o
        ):
            lines.append(f"UNKNOWN {raw} script path appears twice")
            continue
        if not os.path.isfile(path) or os.path.islink(path):
            lines.append(f"UNKNOWN {raw} not a regular file")
            continue
        try:
            if os.path.getsize(path) > MAX_SCRIPT_BYTES:
                lines.append(f"UNKNOWN {raw} too large to scan")
                continue
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
        except (OSError, UnicodeDecodeError) as err:
            lines.append(f"UNKNOWN {raw} unreadable ({type(err).__name__})")
            continue
        reason = scan_source(source, os.path.dirname(path))
        lines.append(f"WRITES {raw} {reason}" if reason else f"READONLY {raw}")
    return lines


def main_event(stdin_text: str) -> int:
    try:
        event = json.loads(stdin_text)
    except ValueError:
        return 0
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command", "")
    if isinstance(command, str):
        for line in judge(command):
            print(line)
    return 0


def selftest() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        def put(name: str, body: str) -> str:
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            return path

        reader = put(
            "analyse.py",
            "import json, sys, re\nfor p in sys.argv[1:]:\n"
            "    d = json.load(open(p))\n    print(re.compile('x').pattern, len(d))\n"
            "print(open(p, encoding='utf8', errors='replace').read())\n"
            "sys.stdout.write('ok')\n",
        )
        writers = {
            "w_open.py": "open('a','w').write('x')\n",
            "w_mode_kw.py": "open('a', mode='a')\n",
            "w_mode_var.py": "m='w'\nopen('a', m)\n",
            "w_text.py": "import pathlib\npathlib.Path('a').write_text('x')\n",
            "w_path_open.py": "import pathlib\npathlib.Path('a').open('w')\n",
            "w_shutil.py": "import shutil\n",
            "w_sub.py": "import subprocess\n",
            "w_os.py": "import os\nos.replace('a','b')\n",
            "w_from_os.py": "from os import remove\n",
            "w_exec.py": "exec('x')\n",
            "w_getattr.py": "import pathlib\ngetattr(pathlib.Path('a'), 'write_text')('x')\n",
            "w_local.py": "import helper\n",
            "w_rplus.py": "open('a','r+b')\n",
            "w_spec_scratch.py": "import importlib.util\n"
            "importlib.util.spec_from_file_location('a', '/tmp/x/patch.py')\n",
            "w_spec_var.py": "import importlib.util\np='x'\n"
            "importlib.util.spec_from_file_location('a', p)\n",
            "w_syspath.py": "import sys\nsys.path.insert(0, '/tmp/x')\n",
            "w_import_module.py": "import importlib\nimportlib.import_module('subprocess')\n",
        }
        # The shape of the 2026-09-29 analysis script: loads a committed tool as a library.
        put(
            "load_tool.py",
            "import importlib.util, sys\n"
            "sys.path.insert(0, '/home/banon/projects/er-mods-rs/scripts')\n"
            "spec = importlib.util.spec_from_file_location("
            "'a', '/home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py')\n"
            "A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)\n",
        )
        got = judge(f"python3 {os.path.join(tmp, 'load_tool.py')}")
        if len(got) != 1 or not got[0].startswith("READONLY "):
            failures.append(f"load_tool.py: expected READONLY, got {got}")
        put("helper.py", "")
        for name, body in writers.items():
            path = put(name, body)
            got = judge(f"python3 {path}")
            if len(got) != 1 or not got[0].startswith("WRITES "):
                failures.append(f"{name}: expected WRITES, got {got}")
        cases = [
            (f"timeout 29 python3 {reader} {tmp} 2>&1 | cut -c1-260", f"READONLY {reader}"),
            (f"S={tmp}; timeout 29 python3 \"$S/analyse.py\" 2>&1 | tail -20", "READONLY $S/analyse.py"),
            (f"S={tmp}; python3 ${{S}}/analyse.py", "READONLY ${S}/analyse.py"),
            ("timeout 25 python3 $S/analyse.py | head -9", "UNKNOWN $S/analyse.py unresolved variable"),
            (f"S={tmp}; S=/elsewhere; python3 $S/analyse.py", "UNKNOWN $S/analyse.py unresolved variable"),
            (f"cp /x/w.py {reader} && python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"python3 {reader} > {reader}", f"UNKNOWN {reader} script path appears twice"),
            (f"python3 $(echo {reader})", None),
            ("python3 analyse.py", "UNKNOWN analyse.py relative path"),
            (f"python3 {tmp}/missing.py", f"UNKNOWN {tmp}/missing.py not a regular file"),
            ("cargo test -p x", None),
        ]
        for command, want in cases:
            got = judge(command)
            if want is None:
                if any(line.startswith("READONLY") for line in got):
                    failures.append(f"{command!r}: expected no READONLY, got {got}")
            elif not any(line.startswith(want) for line in got):
                failures.append(f"{command!r}: expected {want!r}, got {got}")
        mixed = judge(f"python3 {reader}; python3 {os.path.join(tmp, 'w_open.py')}")
        if sorted(line.split()[0] for line in mixed) != ["READONLY", "WRITES"]:
            failures.append(f"mixed command: {mixed}")
    for failure in failures:
        print("FAIL", failure)
    print("selftest:", "FAIL" if failures else "ok", f"({len(failures)} failures)")
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    if sys.argv[1:]:
        print("usage: cupcake_python_script_writes.py [--selftest]  (event JSON on stdin)")
        sys.exit(2)
    sys.exit(main_event(sys.stdin.read()))
