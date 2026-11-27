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

Two more kinds of line, added 2026-11-16:

    `GUARDTEST <operand>`                      a committed test suite of the user's guard repos
    `EXEC-<verdict>\t<word>\t<path>\t<why>`    a python program run by name, tab-separated

GUARDTEST. The guard refused `python3 ~/.config/cupcake/tests/check_background_tails.py`, the
test suite of the user's global cupcake guards, because it runs subprocesses and writes temp
files. That file is not an edit composed in a turn; it is a committed test of a guard. A script
is admitted as one when its real path is a `.py` file directly in a guard repo's test directory
(`$HOME/.config/cupcake/tests`, or the `os.pathsep` list in `CUPCAKE_GUARD_TEST_DIRS`), not in a
subdirectory of it, and the path exists in that repo's `HEAD` commit. The same directory of a
`git worktree add` checkout of that repo counts too, checked against that checkout's own `HEAD`
(2026-11-26); a checkout belongs to the guard repo when `git rev-parse --git-common-dir` names
the guard repo's git dir, so a clone or a worktree of any other repo does not. An untracked file
dropped there, or one only staged, is scanned like any other script. The working copy may differ
from `HEAD`: an edit to a committed test was made with the Edit tool, which is the diff the guard
wants to see. The command-level fail-closed checks below still apply first.

EXEC. A python program on `PATH` run by its name (`kitty-tail-bg --selftest`), or by a path
(`./x`, `/abs/x`), or handed to python without a `.py` suffix (`python3 /abs/x`), was never
inspected: the guard reads only `python3 <file>.py`, `-c` and heredocs. Every word of the
command that resolves to such a file gets an `EXEC-` line; whether the word stands in command
position is the policy's question, answered by the same tokenizer it uses everywhere else. The
verdict is `READONLY`, `GUARDTEST`, `WRITES` or `UNKNOWN`, decided exactly as for a `.py` operand.
A file counts as python by its `#!` line (`python` or `uv run` in it), or by being a python
interpreter's operand. Files the current user neither owns nor may write (`/usr/bin/pip`) are
skipped: no agent session wrote them. `CUPCAKE_PYTHON_EXEC_PATH` replaces `$PATH` for the lookup,
for tests; otherwise `$PATH` is searched and then `~/.local/bin` and `~/bin`.

`importlib` gets one narrow allowance, because loading a committed tool as a library is how a
scratch analysis reuses it (the 2026-09-29 script loaded `scripts/er-mechanics-ashes.py` with
`spec_from_file_location`): the location must be a string literal naming a file under this
repo's `scripts/` tree by the same path test the policy applies to `python3 <path>.py`, and a
`sys.path` entry must be a literal naming a directory there. Anything else from `importlib`, or
a computed location, counts as a write.

Fail-closed choices on the command, each of which makes every operand `UNKNOWN`:

* a backtick or a process substitution (`<(...)`, `>(...)`);
* a write-capable tool anywhere in the command (`cp`, `mv`, `tee`, ...), since it could
  replace the script between this read and python's. A plain `git rev-parse` does not count,
  since 2026-11-26: it is the one subcommand measured to run no program named in repo config
  (`GIT_READ_ONLY_SUBCOMMANDS`); see `git_reads_only` for what still marks it;
* a `sed` that can write: `-i`/`--in-place`, a script read from a file (`-f`), a script that
  is not a literal (any part outside single quotes holding `$`, a backtick or a glob), or a
  literal script with a `w`/`W`/`e` command, an `s///w` or `s///e` flag, or anything the
  small parser in `sed_script_writes` cannot read. Until 2026-11-20 any word naming `sed`
  marked the command, so `... | sed -n '1,3p;$p'`, which writes nothing, was refused while
  the same pipe through `awk` was allowed;
* the script path occurring anywhere else in the command, or being a redirect target;
* a `$NAME` that is not assigned exactly once, to a literal, earlier in the same command;
* a path that is relative after expansion, when the working directory is not known. It is
  known from the event's `cwd`, followed through each literal `cd` in the command; a `cd` inside
  a subshell, or to a target that does not resolve, makes it unknown for the rest.

A `$(...)` command substitution no longer fails closed on its own (2026-11-20). It refused
`n=$(wc -l < wrap.log); { python3 <abs>/stamp_log.py wrap.log $n ...; tail -n +$((n+1)) ...; }`,
whose substitution only counts lines and whose `$((n+1))` is arithmetic. The tokenizer splits a
substitution's words out of the command (and a quoted one is re-tokenized like `bash -c "..."`),
so every check above already reads them: a write tool inside it, a script operand inside it, or a
redirect inside it onto the script. What a substitution could still do is produce the script
operand itself, and an operand holding `$(` does not expand here, so it stays `UNKNOWN`.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import sys

# `subprocess` and `tempfile` are imported where they are used: this runs on every Bash call
# since 2026-11-16, and the two cost ~18 ms of a ~44 ms run between them (measured with
# `python3 -X importtime`). Only a guard-test lookup needs the first and only the selftest the
# second.

MAX_SCRIPT_BYTES = 1 << 20

# Tools that can put different bytes at the script path inside the same command.
WRITE_TOOLS = {
    "cp", "mv", "ln", "install", "rsync", "tee", "dd", "perl", "patch", "truncate",
    "curl", "wget", "tar", "unzip", "git", "scp", "ruby", "node",
}

# `git` stays a write tool except for these subcommands, which run no program that repo config
# or attributes name (2026-11-26:
# `cd <guard worktree> && git rev-parse HEAD && python3 tests/<x>.py`
# was refused for the `git rev-parse`). Only `rev-parse` qualifies. Measured the
# same day with git 2.56.1 under a tty, in a clone with `core.fsmonitor`, `core.pager`,
# `pager.<cmd>`, `diff.external`, `gpg.program`, `log.showSignature` and `core.hooksPath` hooks
# each set to a command that touches a marker: `git rev-parse HEAD`,
# `--show-toplevel --git-common-dir --abbrev-ref HEAD`,
# `--verify` and `--is-inside-work-tree --show-prefix` ran
# none of them, `git ls-files` ran `core.fsmonitor` and `pager.ls-files`, and `git merge-base`
# ran `pager.merge-base`. `status`, `diff`, `show`, `log`, `cat-file --filters` and `blame` run
# fsmonitor, `diff.external`, textconv or filter drivers, or `gpg.program` by config alone.
# Global options other than these mark the command, since `-c core.pager=...` or `--exec-path`
# can run anything.
GIT_READ_ONLY_SUBCOMMANDS = {"rev-parse"}
GIT_SAFE_GLOBAL_FLAGS = {"--no-pager", "-P", "--no-optional-locks"}
# A name git reads from its environment (`GIT_TRACE=<file>` writes a file, `GIT_CONFIG_*` adds
# config), or one that picks which git or which code runs. It marks every `git` when it appears
# as a word anywhere in the command, quoted or under `bash -c`, other than read as `$NAME`.
GIT_ENV_NAME_RE = re.compile(
    r"(?<![\w$])(?:GIT_\w*|PAGER|EDITOR|VISUAL|LESS\w*|PATH|HOME|XDG_CONFIG_HOME|LD_\w+)(?!\w)"
)
# Commands that can set or export a variable whose name the command never spells out
# (`export $n=...`, `read`, `source env.sh`), or run a string as a command (`eval`). Any of them
# in command position marks every `git`.
GIT_ENV_COMMANDS = {
    "export", "declare", "typeset", "readonly", "local", "env", "read", "mapfile", "readarray",
    "printf", "source", ".", "eval", "exec", "set", "let", "alias", "enable", "builtin",
    "command",
}

# `sed` is judged by its arguments instead (`sed_reason`): `sed -n '1,3p'` writes nothing.
SED_NAMES = {"sed", "gsed"}

# GNU sed's long options, for its unambiguous-prefix matching. The value says what the option
# does here: "write" can write, "script" takes a script, "file" reads one, "arg" takes a value
# that is not a script, "flag" takes none.
SED_LONG_OPTIONS = {
    "in-place": "write", "expression": "script", "file": "file", "line-length": "arg",
    "null-data": "flag", "zero-terminated": "flag", "quiet": "flag", "silent": "flag",
    "regexp-extended": "flag", "separate": "flag", "sandbox": "flag", "unbuffered": "flag",
    "posix": "flag", "debug": "flag", "follow-symlinks": "flag", "binary": "flag",
    "help": "flag", "version": "flag",
}
SED_SHORT_FLAGS = set("nrEsuzb")
# Commands that take nothing, or an optional number (`lLqQ`).
SED_SIMPLE_COMMANDS = set("=dDgGhHnNpPxzF{}")
SED_NUMBER_COMMANDS = set("lLqQ")
# Commands whose argument runs to the end of the command: text, labels, a file to read. Read
# here only up to the next `;` or newline. GNU reads some of them to the end of the line, so
# stopping early can only make this parse more of the script as commands, never less.
SED_TO_END_COMMANDS = set("aicbtT:rRv")
SED_S_FLAGS = set("gpiImM0123456789")

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
REDIRECTS = {">", ">>", "<", "<<", "<<<", ">|", "&>", "&>>", ">&", "<&"}

# Python's options that take the next word as their value, and the two after which no script
# operand follows (`-c` runs a string, `-m` a module).
PYTHON_VALUED_OPTIONS = {"-W", "-X", "-Q"}
PYTHON_NO_SCRIPT_OPTIONS = {"-c", "-m", "-"}

# Words never looked up on PATH: shell builtins and keywords. A hit would be a file of the same
# name that the shell does not run.
SHELL_BUILTINS = {
    "cd", "pushd", "popd", "echo", "printf", "test", "[", "[[", "]]", "true", "false", "read",
    "export", "source", ".", "set", "unset", "exit", "return", "eval", "exec", "type", "command",
    "builtin", "local", "declare", "typeset", "alias", "unalias", "shift", "wait", "trap", "if",
    "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "in",
    "function", "time", "!", "{", "}", ":", "let", "ulimit", "umask", "hash", "jobs", "fg", "bg",
    "kill", "disown", "getopts", "readonly", "mapfile", "readarray",
}
BARE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+-]*$")
SHEBANG_PYTHON_RE = re.compile(rb"python|\buv\b.*\brun\b")
GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


def python_word(word: str) -> bool:
    base = word.rsplit("/", 1)[-1]
    return base.startswith("python")


def guard_test_dirs() -> list[str]:
    """The test directories of the user's guard repos, real paths."""
    override = os.environ.get("CUPCAKE_GUARD_TEST_DIRS")
    if override is not None:
        dirs = [d for d in override.split(os.pathsep) if d]
    else:
        dirs = [os.path.join(os.path.expanduser("~"), ".config", "cupcake", "tests")]
    return [os.path.realpath(d) for d in dirs if os.path.isabs(d)]


def git_read(directory: str, *args: str) -> str | None:
    import subprocess

    env ={k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    try:
        proc = subprocess.run(
            ["git", "-C", directory, *args], capture_output=True, text=True, timeout=2, env=env
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def git_checkout(directory: str) -> tuple[str, str] | None:
    """The top of the checkout holding `directory` and the git dir its worktrees share, real
    paths. A `git worktree add` checkout and its main checkout share the second."""
    out = git_read(
        directory, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"
    )
    lines = out.splitlines() if out else []
    if len(lines) != 2 or not all(os.path.isabs(line) for line in lines):
        return None
    return os.path.realpath(lines[0]), os.path.realpath(lines[1])


def guard_test_file(path: str) -> bool:
    """`path` is a test of a guard repo: a `.py` file directly in one of its test dirs (not in a
    subdirectory, where fixtures live), or in the same directory of another checkout of that
    repo (a `git worktree add` of it), and present in that checkout's `HEAD`.

    Until 2026-11-26 only the test dir itself counted, so the same committed suite in a worktree
    of the guard repo was refused: `cd ~/.config/cupcake-wt-no-cleanup-handoff && timeout 25
    python3 tests/check_cleanup_handoff.py`. A checkout is the guard repo's when its shared git
    dir (`--git-common-dir`) is the guard repo's; a clone or any other repo has its own."""
    real = os.path.realpath(path)
    if not real.endswith(".py"):
        return False
    parent = os.path.dirname(real)
    dirs = [d for d in guard_test_dirs() if os.path.basename(d) == os.path.basename(parent)]
    if not dirs:
        return False
    checkout = git_checkout(parent)
    if checkout is None:
        return False
    top, common = checkout
    rel_dir = os.path.relpath(parent, top)
    if rel_dir.startswith(".."):
        return False
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        guard = checkout if directory == parent else git_checkout(directory)
        if guard is None or guard[1] != common:
            continue
        if os.path.relpath(directory, guard[0]) != rel_dir:
            continue
        rel = os.path.relpath(real, top)
        if git_read(top, "cat-file", "-e", f"HEAD:{rel}") is not None:
            return True
    return False


def search_dirs() -> list[str]:
    raw = os.environ.get("CUPCAKE_PYTHON_EXEC_PATH")
    if raw is None:
        home = os.path.expanduser("~")
        raw = os.pathsep.join(
            [os.environ.get("PATH", ""), os.path.join(home, ".local", "bin"), os.path.join(home, "bin")]
        )
    seen: list[str] = []
    for directory in raw.split(os.pathsep):
        if directory and os.path.isabs(directory) and directory not in seen:
            seen.append(directory)
    return seen


def which(name: str, dirs: list[str]) -> str | None:
    for directory in dirs:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def agent_writable(path: str) -> bool:
    """A file an agent session running as this user could have written."""
    try:
        info = os.stat(path)
    except OSError:
        return False
    return info.st_uid == os.getuid() or os.access(path, os.W_OK)


def python_shebang(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            first = handle.readline(256)
    except OSError:
        return False
    return first.startswith(b"#!") and bool(SHEBANG_PYTHON_RE.search(first))


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
        name = match.group(1) or match.group(2)
        # `$HOME` not assigned in the command is the shell's own, which is this process's: the
        # spelling AGENTS.md asks for, `$HOME/.local/bin/bd`, was refused as unresolved before.
        value = env.get(name) if name in env or name != "HOME" else os.path.expanduser("~")
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


def checkout_hops(hops: list[str]) -> bool:
    """Nothing, or `.worktrees/<name>` / `.claude/worktrees/<name>` repeated: the policy's
    `checkout_hops`."""
    i = 0
    while i < len(hops):
        if hops[i] == ".worktrees":
            i += 1
        elif hops[i] == ".claude" and hops[i + 1 : i + 2] == ["worktrees"]:
            i += 2
        else:
            return False
        if i >= len(hops) or hops[i] in {"", "."}:
            return False
        i += 1
    return True


def committed_script_path(path: str) -> bool:
    """The policy's `committed_script_path` for an absolute path: `.../er-mods-rs/scripts/<x>`,
    with only checkout hops (`.worktrees/<name>`, `.claude/worktrees/<name>`) allowed between.
    Until 2026-11-19 any `.../er-mods-rs/<anything>/scripts/<x>` passed, so a `sys.path` entry
    or importlib location naming a copy of the tool tree under `target/` was trusted."""
    if not path.startswith("/") or ".." in path:
        return False
    segments = path.split("/")
    for repo_index, segment in enumerate(segments):
        if segment != "er-mods-rs":
            continue
        for scripts_index in range(repo_index + 1, len(segments) - 1):
            if segments[scripts_index] == "scripts" and checkout_hops(
                segments[repo_index + 1 : scripts_index]
            ):
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


def token_lists(tokens: list[str], depth: int = 0):
    """The command's words, then the words of each word that is itself a command line
    (`bash -c "kitty-tail-bg --selftest"`), three levels deep."""
    yield tokens
    if depth >= 3:
        return
    for word in tokens:
        if any(ch.isspace() for ch in word):
            sub = tokenize(word)
            if sub and sub != [word]:
                yield from token_lists(sub, depth + 1)


def cwd_track(tokens: list[str], cwd: str | None, env: dict[str, str | None]) -> list[str | None]:
    """The working directory in effect at each word, or None where it is not known."""
    current = os.path.normpath(cwd) if cwd and os.path.isabs(cwd) else None
    out: list[str | None] = []
    depth = 0
    start = True
    for index, word in enumerate(tokens):
        out.append(current)
        if word in SEPARATORS or (word and set(word) <= set("&|;()")):
            depth += word.count("(") - word.count(")")
            start = True
            continue
        if start and ASSIGN_RE.match(word):
            continue
        if start and word in {"cd", "pushd", "popd"}:
            target = tokens[index + 1] if index + 1 < len(tokens) else None
            if target is not None and (target in SEPARATORS or set(target) <= set("&|;()")):
                target = None
            current = change_dir(current, word, target, env, depth)
        start = False
    return out


def change_dir(
    current: str | None, verb: str, target: str | None, env: dict[str, str | None], depth: int
) -> str | None:
    if depth > 0 or verb == "popd":
        return None
    if target is None:
        return os.path.expanduser("~")
    if target.startswith("-"):
        return None
    value = expand(target, env)
    if value is None:
        return None
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(current, value)) if current else None


def file_verdict(path: str) -> tuple[str, str]:
    """`GUARDTEST`, `READONLY`, `WRITES` or `UNKNOWN` for a file already pinned down, with the reason."""
    if guard_test_file(path):
        return "GUARDTEST", ""
    try:
        if os.path.getsize(path) > MAX_SCRIPT_BYTES:
            return "UNKNOWN", "too large to scan"
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
    except (OSError, UnicodeDecodeError) as err:
        return "UNKNOWN", f"unreadable ({type(err).__name__})"
    reason = scan_source(source, os.path.dirname(path))
    return ("WRITES", reason) if reason else ("READONLY", "")


def python_operands_without_suffix(tokens: list[str]) -> set[int]:
    """Indexes of each word a python interpreter runs as its script that does not end in `.py`."""
    found: set[int] = set()
    for index, word in enumerate(tokens):
        if not python_word(word):
            continue
        j = index + 1
        while j < len(tokens):
            nxt = tokens[j]
            if nxt in SEPARATORS or nxt in REDIRECTS or set(nxt) <= set("<>&|;()"):
                break
            if nxt in PYTHON_NO_SCRIPT_OPTIONS or (
                nxt.startswith("-") and not nxt.startswith("--") and nxt[-1] in "cm"
            ):
                break
            if nxt in PYTHON_VALUED_OPTIONS:
                j += 2
                continue
            if nxt.startswith("-"):
                j += 1
                continue
            if not nxt.endswith(".py"):
                found.add(j)
            break
    return found


def redirect_targets(tokens: list[str], cwds: list[str | None], env: dict[str, str | None]) -> set[str]:
    out = set()
    for index, word in enumerate(tokens[:-1]):
        if word not in REDIRECTS and not (word and set(word) <= set("<>&") and ">" in word):
            continue
        value = expand(tokens[index + 1], env)
        if not value:
            continue
        cwd = cwds[index + 1]
        if not os.path.isabs(value):
            if cwd is None:
                continue
            value = os.path.join(cwd, value)
        out.add(os.path.realpath(value))
    return out


def exec_lines(
    tokens: list[str],
    skip: set[int],
    cwds: list[str | None],
    env: dict[str, str | None],
    command_reason: str | None,
    dirs: list[str],
) -> list[str]:
    """`EXEC-` lines for each word that names a python program to run by name or path."""
    forced = python_operands_without_suffix(tokens)
    targets: set[str] | None = None
    lines = []
    for index, raw in enumerate(tokens):
        if index in skip or not raw or raw.startswith("-") or python_word(raw):
            continue
        if raw in SEPARATORS or set(raw) <= set("<>&|;()") or any(ch.isspace() for ch in raw):
            continue
        if index > 0 and tokens[index - 1] in REDIRECTS:
            continue
        if ASSIGN_RE.match(raw):
            continue
        is_forced = index in forced
        word = expand(raw, env)
        # Only a word that is surely python fails closed when it cannot be pinned down: any other
        # `$X/tool` or `./tool` may be anything, and refusing those would refuse ordinary commands.
        pathlike = is_forced or raw.endswith(".py")
        if word is None:
            if pathlike:
                lines.append(f"EXEC-UNKNOWN\t{raw}\t-\tunresolved variable")
            continue
        if is_forced or "/" in word:
            if not os.path.isabs(word):
                cwd = cwds[index]
                if cwd is None:
                    if pathlike:
                        lines.append(f"EXEC-UNKNOWN\t{raw}\t-\trelative path, working directory unknown")
                    continue
                word = os.path.join(cwd, word)
            path = os.path.normpath(word)
        else:
            if word in SHELL_BUILTINS or not BARE_NAME_RE.match(word):
                continue
            found = which(word, dirs)
            if found is None:
                continue
            path = found
        if not os.path.isfile(path):
            continue
        real = os.path.realpath(path)
        if not agent_writable(real):
            continue
        if not is_forced and not python_shebang(real):
            continue
        if command_reason:
            kind, why = "UNKNOWN", command_reason
        else:
            if targets is None:
                targets = redirect_targets(tokens, cwds, env)
            if real in targets:
                kind, why = "UNKNOWN", "the program is a redirect target in the same command"
            else:
                kind, why = file_verdict(real)
        lines.append(f"EXEC-{kind}\t{raw}\t{real}\t{why}")
    return lines


def quoted_words(text: str) -> list[tuple[str, bool | None]] | None:
    """The shell words of `text` as `(value, literal)`, and each operator run as `(op, None)`.
    `literal` is False when a part of the word outside single quotes holds `$`, a backtick or a
    glob or brace character, i.e. when the shell may hand the program something else. shlex
    drops the quotes this needs: `'$p'` (sed's last line) and `$p` come out the same. None when
    a quote does not close."""
    out: list[tuple[str, bool | None]] = []
    buf: list[str] = []
    literal = True
    in_word = False
    i, n = 0, len(text)

    def end() -> None:
        nonlocal buf, literal, in_word
        if in_word:
            out.append(("".join(buf), literal))
        buf, literal, in_word = [], True, False

    while i < n:
        c = text[i]
        if c in " \t":
            end()
            i += 1
            continue
        if c == "\n" or c in ";&|()<>":
            end()
            j = i + 1
            while c != "\n" and j < n and text[j] in ";&|()<>":
                j += 1
            out.append((text[i:j], None))
            i = j
            continue
        if c == "#" and not in_word:
            while i < n and text[i] != "\n":
                i += 1
            continue
        in_word = True
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return None
            buf.append(text[i + 1 : j])
            i = j + 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1] if text[j + 1] in '$`"\\\n' else text[j : j + 2])
                    j += 2
                    continue
                if text[j] in "$`":
                    literal = False
                buf.append(text[j])
                j += 1
            if j >= n:
                return None
            i = j + 1
        elif c == "\\":
            buf.append(text[i + 1 : i + 2])
            i += 2
        else:
            if c in "$`*?[{":
                literal = False
            buf.append(c)
            i += 1
    end()
    return out


def sed_reason(command: str, nonliteral: bool = False, depth: int = 0) -> str | None:
    """Why a `sed` in `command` can write, or None when none can. Each word holding whitespace is
    read again as a command line, three levels deep, the way `token_lists` reads them."""
    words = quoted_words(command)
    if words is None:
        names_sed = any(w.rsplit("/", 1)[-1] in SED_NAMES for w in re.split(r"[\s;&|()<>'\"]+", command))
        return "sed, in a command whose quoting this cannot read" if names_sed else None
    for index, (value, literal) in enumerate(words):
        if literal is None:
            continue
        if value.rsplit("/", 1)[-1] in SED_NAMES:
            reason = sed_invocation_writes(words, index, nonliteral)
            if reason:
                return reason
        if depth < 3 and any(ch.isspace() for ch in value):
            reason = sed_reason(value, nonliteral or not literal, depth + 1)
            if reason:
                return reason
    return None


def sed_long_option(name: str) -> str | None:
    """GNU's prefix matching: the option `name` abbreviates, when exactly one."""
    if name in SED_LONG_OPTIONS:
        return name
    hits = [option for option in SED_LONG_OPTIONS if option.startswith(name)]
    return hits[0] if len(hits) == 1 and name else None


def sed_invocation_writes(
    words: list[tuple[str, bool | None]], index: int, nonliteral: bool
) -> str | None:
    """Why the `sed` at `words[index]` can write, or None. GNU sed permutes its arguments, so an
    option after the script (`sed 's/a/b/' -i f`) counts; `--` ends the options."""
    scripts: list[tuple[str, bool]] = []
    positional: list[tuple[str, bool]] = []
    options_done = False
    j = index + 1

    def next_word(k: int) -> tuple[str, bool] | None:
        if k < len(words) and words[k][1] is not None:
            return words[k][0], bool(words[k][1])
        return None

    while j < len(words):
        value, literal = words[j]
        if literal is None:
            if set(value) <= set("<>&") and ("<" in value or ">" in value):
                j += 2  # a redirect and its target, which the shell opens, not sed
                continue
            break
        literal = literal and not nonliteral
        if options_done or value == "-" or not value.startswith("-"):
            positional.append((value, literal))
            j += 1
            continue
        if value == "--":
            options_done = True
            j += 1
            continue
        if not literal:
            return "sed with an option that is not a literal"
        if value.startswith("--"):
            name, has_value, arg = value[2:].partition("=")
            option = sed_long_option(name)
            if option is None:
                return f"sed option {value} not known here"
            kind = SED_LONG_OPTIONS[option]
            if kind == "write":
                return f"sed --{option} writes the file in place"
            if kind == "file":
                return "sed reads its script from a file"
            if kind in {"script", "arg"} and not has_value:
                taken = next_word(j + 1)
                if taken is None:
                    return f"sed --{option} with no value"
                arg, literal = taken[0], taken[1] and not nonliteral
                j += 1
            if kind == "script":
                scripts.append((arg, literal))
            j += 1
            continue
        k = 1
        while k < len(value):
            flag = value[k]
            if flag == "i":
                return "sed -i writes the file in place"
            if flag in "efl":
                arg = value[k + 1 :]
                arg_literal = literal
                if not arg:
                    taken = next_word(j + 1)
                    if taken is None:
                        return f"sed -{flag} with no value"
                    arg, arg_literal = taken[0], taken[1] and not nonliteral
                    j += 1
                if flag == "f":
                    return "sed reads its script from a file"
                if flag == "e":
                    scripts.append((arg, arg_literal))
                break
            if flag not in SED_SHORT_FLAGS:
                return f"sed option -{flag} not known here"
            k += 1
        j += 1
    if not scripts and positional:
        scripts = [positional[0]]
    for script, literal in scripts:
        if not literal:
            return "sed with a script that is not a literal"
        reason = sed_script_writes(script)
        if reason:
            return reason
    return None


def sed_script_writes(script: str) -> str | None:
    """Why a literal GNU sed script can write or run a command, or None. Anything this does not
    parse is answered as a write."""
    s, n = script, len(script)

    def delimited(i: int, delim: str) -> int:
        """The index after the unescaped `delim` closing the part that starts at `i`, or -1."""
        while i < n:
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == "\n":
                return -1
            if s[i] == delim:
                return i + 1
            i += 1
        return -1

    def address(i: int) -> int:
        if i < n and s[i].isdigit():
            while i < n and (s[i].isdigit() or s[i] == "~"):
                i += 1
            return i
        if i < n and s[i] == "$":
            return i + 1
        if i < n and s[i] == "/":
            i = delimited(i + 1, "/")
        elif i + 1 < n and s[i] == "\\" and s[i + 1] not in "\n\\":
            i = delimited(i + 2, s[i + 1])
        else:
            return i
        if i < 0:
            return -1
        while i < n and s[i] in "IM":
            i += 1
        return i

    def to_end(i: int) -> int:
        while i < n and s[i] not in ";\n":
            i += 1
        return i

    i = 0
    while True:
        while i < n and s[i] in " \t\n;":
            i += 1
        if i >= n:
            return None
        if s[i] == "#":
            i = s.find("\n", i)
            if i < 0:
                return None
            continue
        start = i
        i = address(i)
        if i >= 0 and i > start and i < n and s[i] == ",":
            i += 1
            while i < n and s[i] in " \t":
                i += 1
            if i < n and s[i] in "+~":
                i += 1
                while i < n and s[i].isdigit():
                    i += 1
            else:
                i = address(i)
        if i < 0:
            return "a sed address this cannot parse"
        while i < n and s[i] in " \t!":
            i += 1
        if i >= n:
            return "a sed address with no command"
        command = s[i]
        i += 1
        if command in SED_SIMPLE_COMMANDS:
            continue
        if command in SED_NUMBER_COMMANDS:
            while i < n and s[i] in " \t0123456789":
                i += 1
            continue
        if command in "wW":
            return f"sed `{command}` command writes a file"
        if command == "e":
            return "sed `e` command runs a shell command"
        if command in SED_TO_END_COMMANDS:
            i = to_end(i)
            continue
        if command in "sy":
            if i >= n or s[i] in "\\\n":
                return f"a sed `{command}` this cannot parse"
            delim = s[i]
            i = delimited(i + 1, delim)
            if i >= 0:
                i = delimited(i, delim)
            if i < 0:
                return f"a sed `{command}` this cannot parse"
            while command == "s" and i < n and s[i] not in ";\n} \t#":
                if s[i] == "w":
                    return "sed s///w flag writes a file"
                if s[i] == "e":
                    return "sed s///e flag runs a shell command"
                if s[i] not in SED_S_FLAGS:
                    return f"sed s/// flag {s[i]!r} not known here"
                i += 1
            continue
        return f"sed command {command!r} not known here"


def ends_command(word: str) -> bool:
    """`word` ends a simple command: a separator, or punctuation run together (`)&&`) that is
    not a redirect."""
    return word in SEPARATORS or (
        bool(word) and set(word) <= set("<>&|;()") and word not in REDIRECTS
    )


def command_position(tokens: list[str], index: int) -> bool:
    """`tokens[index]` is the first word of a simple command, with nothing before it: no
    assignment, no `env`, `eval`, `xargs`, `timeout` or `-exec` that would run it."""
    return index == 0 or ends_command(tokens[index - 1])


def git_env_changed(command: str, lists: list[list[str]]) -> bool:
    """Something in the command can change git's environment: a name from `GIT_ENV_NAME_RE`
    anywhere in its text, or a command from `GIT_ENV_COMMANDS` in command position. Until
    2026-11-26 only an assignment in the git's own word list counted, so `export
    GIT_EXTERNAL_DIFF=/x; bash -c "git diff"` passed."""
    if GIT_ENV_NAME_RE.search(command):
        return True
    return any(
        word in GIT_ENV_COMMANDS and command_position(toks, i)
        for toks in lists
        for i, word in enumerate(toks)
    )


def git_reads_only(tokens: list[str], index: int, nested: bool, env_changed: bool) -> bool:
    """The `git` at `tokens[index]` runs only `git rev-parse`: the word is exactly `git` (found
    on `PATH`, not `./git`), in command position of the command line itself (`nested` is a
    word list read out of a `bash -c`, `sh -c`, `eval` or quoted string, which counts as not),
    nothing in the command changes git's environment (`env_changed`), only `-C <dir>` and the
    flags in `GIT_SAFE_GLOBAL_FLAGS` come before the subcommand, and no word of the git command
    holds `$` or a backtick, whose value this cannot see (2026-11-26: `o=--output=R/reader.py;
    git diff $o` passed while `diff` was exempt)."""
    if nested or env_changed or tokens[index] != "git" or not command_position(tokens, index):
        return False
    end = index + 1
    while end < len(tokens) and not ends_command(tokens[end]):
        end += 1
    words = tokens[index + 1 : end]
    if any("$" in word or "`" in word for word in words):
        return False
    j = 0
    while j < len(words):
        if words[j] == "-C" and j + 1 < len(words):
            j += 2
        elif words[j] in GIT_SAFE_GLOBAL_FLAGS:
            j += 1
        else:
            break
    return j < len(words) and words[j] in GIT_READ_ONLY_SUBCOMMANDS


def write_tool_reason(command: str, lists: list[list[str]]) -> str | None:
    env_changed = git_env_changed(command, lists)
    for depth_index, toks in enumerate(lists):
        for index, word in enumerate(toks):
            name = word.rsplit("/", 1)[-1]
            if name not in WRITE_TOOLS:
                continue
            if name == "git" and git_reads_only(toks, index, depth_index > 0, env_changed):
                continue
            return "a write-capable tool in the same command"
    return None


def judge(command: str, cwd: str | None = None) -> list[str]:
    tokens = tokenize(command)
    if tokens is None:
        return []
    lists = list(token_lists(tokens))
    every_word = [word for toks in lists for word in toks]
    command_reason = None
    if "`" in command or "<(" in command or ">(" in command:
        command_reason = "command substitution"
    elif write_tool_reason(command, lists):
        command_reason = "a write-capable tool in the same command"
    else:
        sed_why = sed_reason(command)
        if sed_why:
            command_reason = f"a write-capable tool in the same command ({sed_why})"
    env = assignments(every_word)
    has_cd = any(t in {"cd", "pushd", "popd"} for t in tokens)
    dirs = search_dirs()
    lines: list[str] = []
    for depth_index, toks in enumerate(lists):
        cwds = cwd_track(toks, cwd if depth_index == 0 or not has_cd else None, env)
        operands = script_operands(toks)
        lines.extend(exec_lines(toks, set(operands), cwds, env, command_reason, dirs))
        for index in operands:
            raw = toks[index]
            if command_reason:
                lines.append(f"UNKNOWN {raw} {command_reason}")
                continue
            path = expand(raw, env)
            if path is None:
                lines.append(f"UNKNOWN {raw} unresolved variable")
                continue
            if not os.path.isabs(path):
                if cwds[index] is None:
                    lines.append(f"UNKNOWN {raw} relative path")
                    continue
                path = os.path.join(cwds[index], path)
            path = os.path.normpath(path)
            others = [expand(t, env) for i, t in enumerate(toks) if i != index]
            if any(t == raw for i, t in enumerate(toks) if i != index) or any(
                o is not None and os.path.normpath(o) == path for o in others if o
            ):
                lines.append(f"UNKNOWN {raw} script path appears twice")
                continue
            if os.path.realpath(path) in redirect_targets(toks, cwds, env):
                lines.append(f"UNKNOWN {raw} the script is a redirect target in the same command")
                continue
            if not os.path.isfile(path) or os.path.islink(path):
                lines.append(f"UNKNOWN {raw} not a regular file")
                continue
            kind, why = file_verdict(path)
            lines.append(f"{kind} {raw} {why}".rstrip() if kind in {"WRITES", "UNKNOWN"} else f"{kind} {raw}")
    return list(dict.fromkeys(lines))


def main_event(stdin_text: str) -> int:
    try:
        event = json.loads(stdin_text)
    except ValueError:
        return 0
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command", "")
    cwd = event.get("cwd")
    if isinstance(command, str):
        for line in judge(command, cwd if isinstance(cwd, str) else None):
            print(line)
    return 0


def selftest_exec_and_guard_tests() -> list[str]:
    """The two 2026-11-16 additions: programs run by name, and committed guard test suites."""
    import tempfile

    failures: list[str] = []
    saved = {k: os.environ.get(k) for k in ("CUPCAKE_PYTHON_EXEC_PATH", "CUPCAKE_GUARD_TEST_DIRS")}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            guard_dir = os.path.join(tmp, "guard", "tests")
            os.makedirs(bin_dir)
            os.makedirs(guard_dir)

            def put(path: str, body: str, mode: int = 0o755) -> str:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(body)
                os.chmod(path, mode)
                return path

            # The shape of ~/.local/bin/kitty-tail-bg: a python program on `PATH`, no suffix.
            writes = put(os.path.join(bin_dir, "tool-writes"),
                         "#!/usr/bin/env python3\nimport subprocess, tempfile\n")
            reads = put(os.path.join(bin_dir, "tool-reads"),
                        "#!/usr/bin/env python3\nimport sys\nprint(sys.argv)\n")
            uv_script = put(os.path.join(bin_dir, "tool-uv"),
                            "#!/usr/bin/env -S uv run --script\nopen('a', 'w')\n")
            put(os.path.join(bin_dir, "tool-sh"), "#!/bin/bash\nrm -f x\n")
            bare = put(os.path.join(bin_dir, "no-shebang"), "open('a','w')\n", 0o644)
            direct_py = put(os.path.join(bin_dir, "patch.py"),
                            "#!/usr/bin/env python3\nopen('a','w')\n")
            # Not in any git repo, so not a committed guard test: scanned like any script.
            loose_test = put(os.path.join(guard_dir, "check_x.py"),
                             "import subprocess, tempfile\n", 0o644)
            os.environ["CUPCAKE_PYTHON_EXEC_PATH"] = bin_dir
            os.environ["CUPCAKE_GUARD_TEST_DIRS"] = guard_dir

            def has(command: str, prefix: str, cwd: str | None = None) -> None:
                got = judge(command, cwd)
                if not any(line.startswith(prefix) for line in got):
                    failures.append(f"{command!r} (cwd {cwd}): expected {prefix!r}, got {got}")

            def lacks(command: str, prefix: str, cwd: str | None = None) -> None:
                got = judge(command, cwd)
                if any(line.startswith(prefix) for line in got):
                    failures.append(f"{command!r} (cwd {cwd}): expected no {prefix!r}, got {got}")

            has("tool-writes --selftest", f"EXEC-WRITES\ttool-writes\t{writes}\timports subprocess")
            has("timeout 9 tool-writes --dry-run x", "EXEC-WRITES\ttool-writes\t")
            has('bash -c "tool-writes --selftest"', "EXEC-WRITES\ttool-writes\t")
            has("tool-reads a b", f"EXEC-READONLY\ttool-reads\t{reads}")
            has("tool-uv", f"EXEC-WRITES\ttool-uv\t{uv_script}")
            lacks("tool-sh", "EXEC-")
            lacks("tool-missing --x", "EXEC-")
            lacks("echo tool-sh > out.txt", "EXEC-")
            has(f"{writes} --selftest", f"EXEC-WRITES\t{writes}\t")
            has("./tool-writes", "EXEC-WRITES\t./tool-writes\t", bin_dir)
            has(f"cd {bin_dir} && ./tool-writes", "EXEC-WRITES\t./tool-writes\t", "/")
            has(f"cd {tmp} && cd bin && ./patch.py", f"EXEC-WRITES\t./patch.py\t{direct_py}", "/")
            has("( cd /elsewhere ); ./patch.py", "EXEC-UNKNOWN\t./patch.py\t-\trelative path", bin_dir)
            has("./patch.py", "EXEC-UNKNOWN\t./patch.py\t-\trelative path")
            has("$S/patch.py", "EXEC-UNKNOWN\t$S/patch.py\t-\tunresolved variable")
            has(f"S={bin_dir}; $S/tool-writes", "EXEC-WRITES\t$S/tool-writes\t")
            # An unresolved non-.py word may be any program, so it gets no line (2026-11-16:
            # `$HOME/.local/bin/bd remember ...` was refused before `$HOME` resolved).
            lacks("$X/bd remember k", "EXEC-")
            lacks("./tool-writes", "EXEC-")
            saved_home = os.environ.get("HOME", "")
            os.environ["HOME"] = tmp
            try:
                os.makedirs(os.path.join(tmp, ".local", "bin"), exist_ok=True)
                put(os.path.join(tmp, ".local", "bin", "bd"), "#!/bin/sh\n")
                lacks("$HOME/.local/bin/bd remember --key k 'x'", "EXEC-")
                has("$HOME/bin/tool-writes --x", f"EXEC-WRITES\t$HOME/bin/tool-writes\t{writes}")
                # A `HOME` the command assigns is the command's, not this process's.
                lacks("HOME=/nowhere; $HOME/bin/tool-writes", "EXEC-WRITES")
            finally:
                os.environ["HOME"] = saved_home
            # Handed to python with no `.py` suffix: python reads it whatever its first line.
            has(f"python3 {bare}", f"EXEC-WRITES\t{bare}\t{bare}\topen() mode")
            has(f"python3 -u {writes} --x", f"EXEC-WRITES\t{writes}\t")
            has("python3 no-shebang", "EXEC-WRITES\tno-shebang\t", bin_dir)
            # After `-m` or `-c` no word is python's script. (A shebang file named there still
            # gets its line; whether it stands in command position is the policy's question.)
            lacks("python3 -m tool_writes no-shebang", "EXEC-", bin_dir)
            lacks("python3 -c 'print(1)' no-shebang", "EXEC-", bin_dir)
            lacks("python3 -W ignore -m x no-shebang", "EXEC-", bin_dir)
            has(f"cp a b; tool-reads", "EXEC-UNKNOWN\ttool-reads\t")
            has(f"tool-reads > {reads}", "EXEC-UNKNOWN\ttool-reads\t")
            # A `.py` operand of python is the old path's, not an EXEC line.
            lacks(f"python3 {direct_py}", "EXEC-")
            has(f"python3 {direct_py}", f"WRITES {direct_py}")
            # A relative `.py` operand now resolves against the working directory.
            has("python3 patch.py", "WRITES patch.py", bin_dir)
            # Guard test dirs: an uncommitted file there is not a committed test.
            has(f"python3 {loose_test}", f"WRITES {loose_test} imports subprocess")
            lacks(f"python3 {loose_test}", "GUARDTEST")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # Committed guard tests, against the real repos when they exist here. The fixture would
    # otherwise need a commit made by this test, which agents in this repo do not do.
    home_guard = os.path.join(os.path.expanduser("~"), ".config", "cupcake", "tests")
    suite = os.path.join(home_guard, "check_background_tails.py")
    if os.path.isfile(suite) and guard_test_file(suite):
        for command, want in [
            (f"python3 {suite}", f"GUARDTEST {suite}"),
            (f"python3 {suite} --replay /tmp/x.jsonl", f"GUARDTEST {suite}"),
            (f"cd {home_guard} && python3 check_background_tails.py", "GUARDTEST check_background_tails.py"),
            (f"{suite}", f"EXEC-GUARDTEST\t{suite}\t{suite}"),
            (f"cp /tmp/p.py {suite}; python3 {suite}", f"UNKNOWN {suite} a write-capable tool"),
        ]:
            got = judge(command, "/")
            if not any(line.startswith(want) for line in got):
                failures.append(f"{command!r}: expected {want!r}, got {got}")
    else:
        print(f"skip: committed guard test ({suite} absent or not in its repo's HEAD)")
    tail = os.path.join(os.path.expanduser("~"), ".local", "bin", "kitty-tail-bg")
    if os.path.isfile(tail) and python_shebang(tail):
        got = judge("kitty-tail-bg --selftest")
        if not any(line.startswith("EXEC-WRITES\tkitty-tail-bg\t") for line in got):
            failures.append(f"kitty-tail-bg --selftest: expected EXEC-WRITES, got {got}")
    else:
        print(f"skip: {tail} absent or not a python program")
    return failures


def selftest_guard_worktrees() -> list[str]:
    """A committed guard test in a `git worktree add` checkout of the guard repo (2026-11-26).

    The fixture clones the real guard repo into a temp dir and adds a worktree of the clone, so
    it has committed tests without this test making a commit. A second clone stands for an
    unrelated repo: its worktree holds the same files, but its git dir is its own."""
    import subprocess
    import tempfile

    source = os.path.join(os.path.expanduser("~"), ".config", "cupcake")
    if git_checkout(source) is None:
        print(f"skip: guard worktrees ({source} is not a git checkout)")
        return []
    failures: list[str] = []
    # A clone gets git's sample hooks only, none enabled; the user's own config stays out.
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def git(*args: str) -> None:
        proc = subprocess.run(["git", *args], capture_output=True, text=True, env=env, timeout=20)
        if proc.returncode != 0:
            raise OSError(f"git {' '.join(args[:2])} exited {proc.returncode}: {proc.stderr.strip()}")

    saved = os.environ.get("CUPCAKE_GUARD_TEST_DIRS")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            guard, other = os.path.join(tmp, "guard"), os.path.join(tmp, "other")
            wt, other_wt = os.path.join(tmp, "guard-wt"), os.path.join(tmp, "other-wt")
            git("clone", "--quiet", "--no-hardlinks", source, guard)
            git("clone", "--quiet", "--no-hardlinks", source, other)
            git("-C", guard, "worktree", "add", "--quiet", "--detach", wt, "HEAD")
            git("-C", other, "worktree", "add", "--quiet", "--detach", other_wt, "HEAD")
            listed = git_read(guard, "ls-tree", "--name-only", "HEAD", "tests/") or ""
            suites = sorted(n for n in listed.splitlines() if n.endswith(".py"))
            if not suites:
                print(f"skip: guard worktrees (no tests/*.py in {source}'s HEAD)")
                return []
            name = os.path.basename(suites[0])
            loose = os.path.join(wt, "tests", "check_selftest_uncommitted.py")
            with open(loose, "w", encoding="utf-8") as handle:
                handle.write("import subprocess\n")
            os.environ["CUPCAKE_GUARD_TEST_DIRS"] = os.path.join(guard, "tests")
            suite = f"tests/{name}"
            admitted = [
                (f"python3 {guard}/tests/{name}", f"GUARDTEST {guard}/tests/{name}"),
                (f"python3 {wt}/tests/{name}", f"GUARDTEST {wt}/tests/{name}"),
                (f"cd {wt} && python3 {suite}", f"GUARDTEST {suite}"),
                # The command refused on 2026-11-26, both halves of it.
                (f"cd {wt} && git rev-parse HEAD && timeout 25 python3 {suite} 2>&1 | tail -3",
                 f"GUARDTEST {suite}"),
            ]
            for command, want in admitted:
                got = judge(command, "/")
                if want not in got:
                    failures.append(f"{command!r}: expected {want!r}, got {got}")
            refused = [
                # Uncommitted in the worktree: scanned like any script.
                (f"python3 {loose}", f"WRITES {loose} imports subprocess"),
                (f"cd {wt} && python3 tests/{os.path.basename(loose)}", "WRITES tests/"),
                # The same committed file in a worktree of another repo, and in that repo.
                (f"python3 {other_wt}/tests/{name}", None),
                (f"cd {other_wt} && python3 {suite}", None),
                (f"python3 {other}/tests/{name}", None),
                # A git that can rewrite the suite still marks the command.
                (f"cd {wt} && git checkout -- {suite} && python3 {suite}",
                 f"UNKNOWN {suite} a write-capable tool"),
            ]
            for command, want in refused:
                got = judge(command, "/")
                if any(line.startswith("GUARDTEST") for line in got):
                    failures.append(f"{command!r}: expected no GUARDTEST, got {got}")
                elif want is not None and not any(line.startswith(want) for line in got):
                    failures.append(f"{command!r}: expected {want!r}, got {got}")
    except (OSError, subprocess.SubprocessError) as err:
        failures.append(f"guard worktree fixture: {type(err).__name__}: {err}")
    finally:
        if saved is None:
            os.environ.pop("CUPCAKE_GUARD_TEST_DIRS", None)
        else:
            os.environ["CUPCAKE_GUARD_TEST_DIRS"] = saved
    return failures


def selftest() -> int:
    import tempfile

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
            # A `scripts` directory somewhere under the repo is not the repo's `scripts/`
            # (2026-11-19: an agent's copy of the tool tree under `target/`).
            "w_syspath_copy.py": "import sys\n"
            "sys.path.insert(0, '/home/banon/projects/er-mods-rs/target/wf/scripts')\n",
            "w_spec_copy.py": "import importlib.util\nimportlib.util.spec_from_file_location("
            "'a', '/home/banon/projects/er-mods-rs/.worktrees/r21/target/wf/scripts/x.py')\n",
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
        # The same load from checkout spellings of the tree, and the path test on its own.
        for path, want in [
            ("/home/banon/projects/er-mods-rs/scripts/x.py", True),
            ("/home/banon/projects/er-mods-rs/scripts/sub/x.py", True),
            ("/home/banon/projects/er-mods-rs/.worktrees/r21/scripts/x.py", True),
            ("/home/banon/projects/er-mods-rs/.claude/worktrees/agent-a/.worktrees/c/scripts/x.py", True),
            ("/home/banon/projects/er-mods-rs/target/wf/scripts/x.py", False),
            ("/home/banon/projects/er-mods-rs/.worktrees/r21/target/wf/splitcap/scripts/x.py", False),
            ("/home/banon/projects/er-mods-rs/.worktrees/scripts/x.py", False),
            ("/home/banon/projects/er-mods-rs/.claude/scripts/x.py", False),
            ("/tmp/claude-1000/-home-banon-projects-er-mods-rs/scripts/x.py", False),
        ]:
            if committed_script_path(path) != want:
                failures.append(f"committed_script_path({path!r}): expected {want}")
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
            # Refused 2026-11-19: a `sed -n` in the pipe marked the command, though it writes
            # nothing. The same pipe through `awk` was allowed.
            (
                f"cd {tmp} && python3 {reader} target/wrap.log 400 /x.only-sets '2026-11-19 18:52:05'"
                " | grep -E 'attack sets|since the start' | sed -n '1,3p;8,10p;$p' | cut -c1-190",
                f"READONLY {reader}",
            ),
            (f"python3 {reader} | sed -E 's/(a|b)+/\\1/g;/^#/d' | sed --quiet -e '/x/,+2p'", f"READONLY {reader}"),
            # Refused 2026-11-20: a `$(...)` that only counts lines, and `$((n+1))` arithmetic.
            (
                f"cd {tmp} && n=$(wc -l < wrap.log); {{ python3 {reader} wrap.log $n /x '<time>';"
                f" tail -n +$((n+1)) -F --pid=2514771 wrap.log | gawk -f {tmp}/live.awk; }} > wrap.stamped.log",
                f"READONLY {reader}",
            ),
            # A sed that can write still marks the command.
            (f"sed -i 's/a/b/' {reader}; python3 {reader}", f"UNKNOWN {reader} a write-capable tool in the same command (sed -i"),
            (f"python3 {reader} | sed 's/a/b/w out'", f"UNKNOWN {reader} a write-capable tool in the same command (sed s///w"),
            (f"sed 's/a/b/' -i x; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"sed --in-pl=.bak p x; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"sed -ni p x; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"python3 {reader} | sed '/x/W out'", f"UNKNOWN {reader} a write-capable tool"),
            (f"python3 {reader} | sed '1e date'", f"UNKNOWN {reader} a write-capable tool"),
            (f"python3 {reader} | sed 's/a/b/e'", f"UNKNOWN {reader} a write-capable tool"),
            (f"python3 {reader} | sed -f edit.sed", f"UNKNOWN {reader} a write-capable tool"),
            (f"python3 {reader} | sed -n \"$P\"", f"UNKNOWN {reader} a write-capable tool"),
            (f"python3 {reader} | sed -n $p", f"UNKNOWN {reader} a write-capable tool"),
            (f"python3 {reader} | sed 's/[/]/x/w out'", f"UNKNOWN {reader} a write-capable tool"),
            (f"bash -c \"sed -i s/a/b/ x\"; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"x=$(sed -i s/a/b/ x); python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            # What a substitution can still do is caught through its words.
            (f"x=$(cp /tmp/w.py {reader}); python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"python3 $(echo {reader})", None),
            (f"x=$(echo > {reader}); python3 {reader}", f"UNKNOWN {reader} script path appears twice"),
            (f"cd {tmp}; x=$(echo > analyse.py); python3 {reader}", f"UNKNOWN {reader} the script is a redirect target"),
            # A plain `git rev-parse` does not mark the command (2026-11-26); a git that can
            # write, or run a program set in config, attributes or the environment, does.
            (f"git rev-parse HEAD && python3 {reader}", f"READONLY {reader}"),
            (f"git -C {tmp} --no-pager -P --no-optional-locks rev-parse --show-toplevel; python3 {reader}",
             f"READONLY {reader}"),
            (f"cd {tmp} && git rev-parse HEAD 2>&1 | head -1 && python3 {reader}", f"READONLY {reader}"),
            # The review of 2026-11-26: config runs `diff.external`, textconv, fsmonitor,
            # filter drivers and `gpg.program` with no flag on the command line.
            (f"cd {tmp} && git diff; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git status && python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git -C {tmp} --no-pager log --oneline -1; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git ls-files; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git cat-file --filters HEAD:x; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git log --show-signature -1; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            # The environment, set anywhere in the command or under another shell.
            (f"export GIT_EXTERNAL_DIFF=/x; bash -c \"git diff\"; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"export GIT_TRACE={reader}; git rev-parse HEAD; python3 {reader}", f"UNKNOWN {reader}"),
            (f"n=GI; export ${{n}}T_TRACE=/x; git rev-parse HEAD; python3 {reader}", f"UNKNOWN {reader}"),
            (f"env X=1 git rev-parse HEAD; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"PATH=/x:/usr/bin; git rev-parse HEAD; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"source {tmp}/env.sh; git rev-parse HEAD; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"bash -c \"git rev-parse HEAD\"; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"sh -c 'git rev-parse HEAD' && python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"eval git rev-parse HEAD; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"timeout 5 git rev-parse HEAD; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"./git rev-parse HEAD; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git() {{ true; }}; git rev-parse HEAD; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            # A word whose value the command computes.
            (f"o=--output={reader}; git diff $o; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"o=HEAD; git rev-parse $o; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"d={tmp}; git -C \"$d\" rev-parse HEAD; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git rev-parse \"$(echo HEAD)\"; python3 {reader}", f"UNKNOWN {reader}"),
            (f"git checkout -- {reader}; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git -c core.pager=cat log; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git diff --output=/x; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git diff --outp=/x; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git show --ext-diff; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"GIT_EXTERNAL_DIFF=/x git diff; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git status; bash -c \"git stash\"; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
            (f"git; python3 {reader}", f"UNKNOWN {reader} a write-capable tool"),
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
    failures.extend(selftest_exec_and_guard_tests())
    failures.extend(selftest_guard_worktrees())
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
