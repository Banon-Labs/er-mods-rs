#!/usr/bin/env python3
"""The unlock command for a cupcake engine that cannot compile its policies, and the Stop check on it.

When a `.rego` file under the policy tree does not compile, `cupcake eval` exits 1 before any guard
runs, and scripts/cupcake-hook.sh refuses every tool call. The agent can then run nothing at all --
not the revert, not the kitty-tab helper -- so the user has to run the fix by hand. Measured
2026-10-19: a subagent left a parse error, the agent could not act, and it then wrapped the revert
for the user in explanatory prose. User directive the same day: when the user must run a command,
the agent's message is that command and nothing else.

This cannot live in Rego, because the Rego is what is broken. It runs from the hook's
engine-failure path, with python3 and git and (when present) the `opa` CLI.

    cupcake_engine_unlock.py command <repo_root> <global_root> <stderr_file>
        Print the one command that restores every policy file the engine failed to compile, or
        nothing when the cause is not a policy file this can restore (a full /tmp, a file broken at
        `HEAD`, a global policy outside any git checkout). Exit 0 either way.

    cupcake_engine_unlock.py stop-ok <command>   (Stop event JSON on stdin)
        Exit 0 when the closing assistant message is exactly <command> -- bare, in one code span,
        or alone in one fenced block -- and 1 otherwise.

Every failure inside this program prints nothing / exits 1, so the hook falls back to the reply it
gave before this existed: a broken helper must not be the thing that wedges the session further.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

# `/tmp/.tmpN9TRjF/claude/no_lint_before_commit.rego:218: rego_parse_error: ...` -- cupcake copies the
# policy tree into a temp directory and hands it to `opa build`, so the error names the copy. The
# copy flattens the layout (`.cupcake/system/commands.rego` appears as `<tmp>/commands.rego`), so the
# part after the temp directory is matched as a path suffix against the real trees.
ERROR_PATH = re.compile(r"/\.tmp[A-Za-z0-9_]+/(\S+?\.rego):\d+")


def rego_files(root: str) -> list[str]:
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("state", ".git")]
        out.extend(os.path.join(base, f) for f in files if f.endswith(".rego"))
    return out


def opa_fails(path: str) -> bool | None:
    """True when `opa check` rejects the file on its own, None when opa cannot be run."""
    try:
        proc = subprocess.run(["opa", "check", path], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode != 0


def git(root: str, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def toplevel(path: str) -> str | None:
    proc = git(os.path.dirname(path), "rev-parse", "--show-toplevel")
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def broken_files(roots: list[str], stderr_text: str) -> list[str]:
    candidates = [f for root in roots if os.path.isdir(root) for f in rego_files(root)]
    named = sorted(set(ERROR_PATH.findall(stderr_text)))
    found: list[str] = []
    if named:
        for rel in named:
            matches = [f for f in candidates if f.endswith("/" + rel)]
            if len(matches) > 1:
                failing = [f for f in matches if opa_fails(f)]
                matches = failing or matches
            if not matches:
                return []  # the engine named a file this cannot find: no command is better than a wrong one
            found.extend(matches)
    # `opa build` names only the errors of the first file(s) it gave up on -- measured 2026-10-19
    # with a broken tracked file and a broken untracked one: the error listed the first alone. A
    # command restoring only that file would leave the engine broken. So every file git says
    # changed or is untracked is also checked alone, and the ones `opa check` rejects are added.
    # This also covers an error that named no file at all.
    for root in roots:
        for f in changed_rego(root):
            if f not in found and opa_fails(f):
                found.append(f)
    return sorted(set(os.path.realpath(f) for f in found))


def changed_rego(root: str) -> list[str]:
    """Absolute paths of the `.rego` files under `ROOT` that git reports modified or untracked."""
    if not os.path.isdir(root):
        return []
    top = toplevel(os.path.join(root, "."))
    if top is None:
        return []
    status = git(top, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", os.path.realpath(root))
    if status is None or status.returncode != 0:
        return []
    out = []
    entries = status.stdout.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":
            i += 1  # a rename carries its old path as the next entry
        path = os.path.join(top, entry[3:])
        if path.endswith(".rego") and os.path.isfile(path) and "/state/" not in path:
            out.append(path)
    return out


def unlock_command(files: list[str]) -> str:
    """`git -C <top> checkout HEAD -- <files>` per checkout, `rm -f <files>` for untracked ones.

    `checkout HEAD` rather than `checkout --`: the second restores from the index, and a broken
    edit that was staged would come straight back.
    """
    restore: dict[str, list[str]] = {}
    remove: list[str] = []
    for f in files:
        top = toplevel(f)
        if top is None:
            return ""
        tracked = git(top, "ls-files", "--error-unmatch", "--", f)
        if tracked is None:
            return ""
        if tracked.returncode != 0:
            remove.append(f)
            continue
        changed = git(top, "diff", "--quiet", "HEAD", "--", f)
        if changed is None or changed.returncode != 1:
            return ""  # broken at HEAD: restoring it changes nothing
        restore.setdefault(top, []).append(f)
    parts = [
        "git -C " + shlex.quote(top) + " checkout HEAD -- " + " ".join(shlex.quote(f) for f in fs)
        for top, fs in sorted(restore.items())
    ]
    if remove:
        parts.append("rm -f " + " ".join(shlex.quote(f) for f in remove))
    return " && ".join(parts)


def closing_message(event: dict) -> str | None:
    message = event.get("last_assistant_message")
    if isinstance(message, str):
        return message
    path = event.get("transcript_path")
    if not isinstance(path, str) or not os.path.isfile(path):
        return None
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cupcake_turn_scan as scan  # noqa: E402  (a failure here exits 1 = block)

    turn = scan.last_text_turn(scan.split_turns(scan.load_events(path)))
    if turn is None:
        return None
    index = turn.last_text_index
    if index < 0 or turn.tool_after(index):
        return None
    return turn.text_runs[-1]


def is_exactly(message: str, command: str) -> bool:
    text = message.strip()
    if text == command or text == "`" + command + "`":
        return True
    fenced = re.fullmatch(r"```[A-Za-z0-9_-]*\n(.*?)\n?```", text, re.DOTALL)
    return bool(fenced) and fenced.group(1).strip() == command


def main(argv: list[str]) -> int:
    if len(argv) == 5 and argv[1] == "command":
        repo_root, global_root, stderr_file = argv[2:5]
        try:
            with open(stderr_file, encoding="utf-8", errors="replace") as fh:
                stderr_text = fh.read()
        except OSError:
            return 0
        if "OPA compilation failed" not in stderr_text and "rego_" not in stderr_text:
            return 0
        files = broken_files([os.path.join(repo_root, ".cupcake"), global_root], stderr_text)
        if files:
            sys.stdout.write(unlock_command(files))
        return 0
    if len(argv) == 3 and argv[1] == "stop-ok":
        try:
            event = json.loads(sys.stdin.read())
        except ValueError:
            return 1
        message = closing_message(event) if isinstance(event, dict) else None
        return 0 if message is not None and is_exactly(message, argv[2]) else 1
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:  # see the module docstring: fail to the old reply, never wedge further
        sys.exit(1 if len(sys.argv) > 1 and sys.argv[1] == "stop-ok" else 0)
