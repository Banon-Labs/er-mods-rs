#!/usr/bin/env python3
"""Decide whether the pending Bash command may commit, or may run a lint or formatter.

Read by `.cupcake/signals/commit_authorization.sh`, consumed by
`.cupcake/policies/claude/git_commit_authorization.rego` (commits) and
`.cupcake/policies/claude/no_lint_before_commit.rego` (lints and formatters).

User directive 2026-10-17, verbatim: "Lets have a subagent policy to never run lints or formatting
or perform formatting ourselves until we are just about to commit, and only commit when authorized
instead of what your agent just did which is tank my FPS with a commit to a worktree when the files
wouldn't be lost anyway because we are in a virgin worktree in a persistent drive."

So there are two questions, and one authorization answers both:

* A commit is allowed only when the user's own last typed message is exactly `authorize commit`,
  and that message has not already paid for another commit. One message buys one commit.
* A lint or formatter run is allowed only while such a message is live: the user's last typed
  message is `authorize commit` and no commit has spent it yet. That is "just about to commit".
  Lint runs do not spend it; the commit does.

A subagent gets neither, whatever the transcript says.

Output: one tab-separated line, or nothing when stdin is not an event:

    `<commit verdict> <lint verdict> <detail>`

where the commit verdict is `AUTHORIZED`, `DENIED`, or `-` when the command holds no commit, and
the lint verdict is `LIVE` or `DENIED`. Each policy lifts its deny only on its own allowing word,
so a crash, a timeout or empty output keeps both denied.

What counts as the user's message is decided by `cupcake_push_authorization.last_prompt`, shared
rather than copied so the two gates cannot drift apart: the conversation chain is walked back from
this tool call through `parentUuid`, and only a `type: user` entry with `origin.kind == "human"`,
`promptSource` of `typed` or `queued`, not meta, not a compaction summary and not a sidechain,
counts. Assistant text, tool output, task notifications, system reminders and accepted prompt
suggestions never do. Read that module's docstring for the measurements behind each rule.

The commit itself must be one plain `git [-C <dir>] [-c <k>=<v>] commit ...` with nothing a shell
would run around it: no separators, pipes, redirects, heredocs, comments or command
substitutions outside quotes. A message goes in single quotes or through `-F <file>`.

Spent authorizations are recorded in `.cupcake/state/commit-authorizations.jsonl` (gitignored).
`CUPCAKE_COMMIT_AUTHORIZATION_STATE` names a different file; the policy regression runner uses it so
its fixtures never write to this checkout's record. It cannot create an authorization, only choose
where spends are kept, which deleting the file already allows.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cupcake_push_authorization as shared  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_FILE = REPO_ROOT / ".cupcake" / "state" / "commit-authorizations.jsonl"

AUTH_PHRASE = "authorize commit"

# A git commit invocation anywhere in the text, global options allowed between `git` and `commit`.
# Broad on purpose: this only chooses whether the commit verdict is computed, and the policy, not
# this, decides whether the command commits.
COMMIT_RE = re.compile(
    r"(?:^|[\s;&|(`'\"])git(?:\s+(?:-C|-c|--git-dir|--work-tree)(?:=|\s+)\S+|\s+--[\w-]+(?:=\S+)?)*"
    r"\s+commit(?:\s|$|[;&|)`'\"])"
)

UNQUOTED_REFUSED = set(";&|<>()`#\n\r")

# The words `commit_authorization.sh` filters on, applied to the command alone: the signal script
# can only test the whole event, whose transcript path or cwd may hold one of them by accident.
# A command naming none of them is neither a commit nor a lint, and gets no line.
TRIGGER_WORDS = ("commit", "fmt", "clippy", "shellcheck", "ruff", "black", "prettier", "eslint",
                 "isort", "flake8", "pylint", "mypy", "taplo", "dprint", "clang-format", "yamllint",
                 "markdownlint", "stylua", "biome", "comment-caps", "lossy-utf8", "check.sh", "--fix")


class Refused(Exception):
    pass


def state_file() -> Path:
    override = os.environ.get("CUPCAKE_COMMIT_AUTHORIZATION_STATE")
    return Path(override) if override else DEFAULT_STATE_FILE


def emit(commit: str, lint: str, detail: str) -> None:
    detail = detail.replace("\t", " ").replace("\n", " ")
    sys.stdout.write(f"{commit}\t{lint}\t{detail}")


# --- the commit command ------------------------------------------------------------------------

def check_shell_shape(command: str) -> None:
    """Refuse anything a shell would run besides the one command: operators, substitutions."""
    state, i = None, 0
    while i < len(command):
        c = command[i]
        if state == "'":
            if c == "'":
                state = None
        elif state == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                state = None
            elif c == "`" or command.startswith("$(", i):
                raise Refused("the commit message holds a command substitution")
        else:
            if c == "\\":
                i += 1
            elif c in "'\"":
                state = c
            elif c in UNQUOTED_REFUSED or command.startswith("$(", i):
                raise Refused("the commit carries shell syntax around it (a separator, pipe, "
                              "redirect, heredoc, comment or substitution); run it alone as "
                              "`git commit -F <file>` or with a single-quoted `-m`")
        i += 1
    if state is not None:
        raise Refused("the commit command has an unclosed quote")


def parse_commit(command: str) -> None:
    check_shell_shape(command)
    try:
        tokens = shlex.split(command)
    except ValueError:
        raise Refused("the commit command could not be tokenized")
    if not tokens or tokens[0] != "git":
        raise Refused("the command does not start with `git`")
    i = 1
    while i < len(tokens) and tokens[i] in ("-C", "-c"):
        i += 2
    if i >= len(tokens) or tokens[i] != "commit":
        raise Refused("only `git [-C <dir>] commit ...` can be authorized")


# --- the record of spent authorizations ---------------------------------------------------------

def spent_by(message_uuid: str) -> str | None:
    """The tool call that spent this message, or None."""
    try:
        with open(state_file(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("message_uuid") == message_uuid:
                    return str(rec.get("tool_use_id"))
    except OSError:
        return None
    return None


def spend(message_uuid: str, tool_use_id: str) -> bool:
    """Record the spend; False when this message already paid for a different tool call."""
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("message_uuid") == message_uuid:
                return rec.get("tool_use_id") == tool_use_id
        fh.seek(0, os.SEEK_END)
        fh.write(json.dumps({"message_uuid": message_uuid, "tool_use_id": tool_use_id,
                             "at": time.time()}) + "\n")
        return True


# --- the decision ------------------------------------------------------------------------------

def last_typed_prompt(path: str) -> tuple[str, str] | None:
    """(uuid, text) of the last prompt the user typed in the transcript, in file order, or None."""
    transcript = shared.Transcript(path)
    transcript.refresh()
    for e in reversed(transcript.entries()):
        text = shared.prompt_text(e)
        if text is not None and isinstance(e.get("uuid"), str):
            return e["uuid"], text
    return None


def decide(event: dict, started: float) -> tuple[str, str, str] | None:
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    command = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
    if not any(word in command for word in TRIGGER_WORDS):
        return None
    is_commit = COMMIT_RE.search(command) is not None
    commit_denied = "DENIED" if is_commit else "-"

    if event.get("agent_id"):
        return (commit_denied, "DENIED",
                "a subagent never commits and never runs lints or formatters; the main session "
                "does both, and only after the user's `authorize commit`")

    path, tool_use_id = event.get("transcript_path"), event.get("tool_use_id")
    if not isinstance(path, str) or not os.path.isfile(path):
        return commit_denied, "DENIED", "the session transcript is not readable"
    if not isinstance(tool_use_id, str) or not tool_use_id:
        return commit_denied, "DENIED", "the event names no tool call"
    try:
        message_uuid, text = shared.last_prompt(path, tool_use_id,
                                                started + shared.LOCATE_DEADLINE_SECONDS)
    except shared.Refused as r:
        # A lint changes nothing, so it does not need the chain walk a commit needs: when the
        # call's own line has not landed yet (measured 2026-10-17: five authorized lint runs in a
        # row refused this way on a long main-session transcript), the last prompt the user typed
        # anywhere in the transcript answers the same question. A commit still needs the walk.
        found = None if is_commit or "not in the transcript" not in r.reason else last_typed_prompt(path)
        if found is None:
            return commit_denied, "DENIED", r.reason
        message_uuid, text = found

    if text.strip() != AUTH_PHRASE:
        return commit_denied, "DENIED", "the user's last message is not `authorize commit`"

    previous = spent_by(message_uuid)
    lint = "LIVE" if previous is None else "DENIED"
    if not is_commit:
        detail = message_uuid if previous is None else (
            "that `authorize commit` was already spent on a commit")
        return "-", lint, detail

    try:
        parse_commit(command.strip())
    except Refused as r:
        return "DENIED", lint, str(r)
    if not spend(message_uuid, tool_use_id):
        return "DENIED", "DENIED", "that `authorize commit` was already spent on another commit"
    return "AUTHORIZED", lint, message_uuid


def main() -> int:
    started = time.monotonic()
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    try:
        verdict = decide(event, started)
        if verdict is not None:
            emit(*verdict)
    except Exception as exc:  # noqa: BLE001 -- any failure must deny, and say so
        emit("DENIED", "DENIED", f"the authorization reader failed: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
