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

* A commit is allowed only when the last message the user typed is exactly `authorize commit`, and
  that message has not already paid for a commit that landed. One message buys one landed commit.
* A lint or formatter run is allowed only while such a message is live: the last message the user
  typed is `authorize commit` and no commit it paid for has landed. That is "just about to commit".
  Lint runs do not spend it.

A subagent gets neither, whatever the transcript says.

Output: one tab-separated line, or nothing when stdin is not an event:

    `<commit verdict> <lint verdict> <detail>`

where the commit verdict is `AUTHORIZED`, `DENIED`, or `-` when the command holds no commit, and
the lint verdict is `LIVE` or `DENIED`. Each policy lifts its deny only on its own allowing word,
so a crash, a timeout or empty output keeps both denied.

# Which message is the user's

The transcript is read once, with no waiting. In the main session the pending tool call's own
`tool_use` line is not on disk while PreToolUse hooks run: Claude Code writes it after they return
(measured 2026-10-17 and 2026-10-18, bd er-effects-rs-hu4w; a reader that waited 3 s for it refused
commits that way in sessions a66cf759, 7d2f4a9b and e75d8996). So:

1. When the call's line is there, the conversation chain is walked back from it through
   `parentUuid`, as `cupcake_push_authorization.prompt_and_chain` does. A second entry holding the
   same tool call id is refused.
2. When it is not, the chain is walked back from the newest entry in the file instead: what is on
   disk then is the conversation up to this call, and the call descends from its newest entry. An
   entry whose parent is not in the file yet (written ahead of it) is stepped over, up to
   `HEAD_CANDIDATES` of them.

Either way a transcript repeating an entry uuid is refused, the walk ends at a compaction summary or
a root, and it stops at the first genuine typed prompt as `cupcake_push_authorization.prompt_text`
defines one: a `type: user` entry with `origin.kind == "human"`, `promptSource` of `typed` or
`queued`, not meta, not a compaction summary, not a sidechain. Nothing else is a prompt. In
particular the harness nudge "The user hasn't heard from you in a while" is a `type: attachment`
entry with `attachment.type == "silent_turn_reminder"` (1,518 of them in the 40 newest transcripts,
none ever recorded any other way), and task notifications, system reminders, hook output and tool
results fail the test too. `suggestion_accepted` fails on purpose: the model wrote that text and the
user pressed enter. That, not the nudge, is what refused the 2026-10-21 commit in session 7d2f4a9b,
so a refusal says so when it walked past an accepted `authorize commit` suggestion. A message the
user types mid-turn reaches the transcript as a `queued_command` attachment, not a prompt entry, so
it neither authorizes nor revokes (no such message in the 80 newest transcripts was an
`authorize ...`).

What the second path gives up, stated plainly: with no `tool_use_id` to anchor on, an entry
appended to the transcript by something other than Claude Code would be read as the newest one.
`.cupcake/policies/claude/transcript_write_guard.rego` refuses the direct ways of writing it.

# One landed commit per message

The commit itself must be one plain `git [-C <dir>] [-c <k>=<v>] commit ...` with nothing a shell
would run around it: no separators, pipes, redirects, heredocs, comments or command substitutions
outside quotes. A message goes in single quotes or through `-F <file>`.

When a commit is admitted, `.cupcake/state/commit-authorizations.jsonl` (gitignored) records the
message uuid, the tool call, the directory the commit runs in and that repository's state: `HEAD`'s
commit and the size of `HEAD`'s reflog. A commit that lands changes both, and the reflog keeps
growing even when the commit is reset away afterwards. The last record for a message names the call
holding it. A different call is refused while the holder's repository state has changed (its commit
landed, or something else moved `HEAD` there, which counts too: the error goes the closed way) or
cannot be read, and while the holder has not finished: no result on the chain yet, an interrupted
result, or a backgrounded run whose task notification has not arrived. A holder that finished
without changing its repository made no commit, so the new call takes the message over and the
record says which call it replaces. That covers a commit another policy refused before it ran
(2026-10-20) and one the pre-commit hook rejected (2026-10-21, session e75d8996: the reply was spent
on `toolu_018ZgyazJW1ySE2LDBbg42qd`, whose hook failed, and no retry could succeed). The same tool
call evaluated again, by a second hook, is the same commit. A directory that is in no repository
records that, since no commit can land there. A record from before this rule (no repository state)
counts as spent unless Claude Code refused its call before it ran.

`CUPCAKE_COMMIT_AUTHORIZATION_STATE` names a different record file; the policy regression runners
use it so their fixtures never write to this checkout's record. It cannot create an authorization,
only choose where spends are kept, which deleting the file already allows.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cupcake_push_authorization as shared  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_FILE = REPO_ROOT / ".cupcake" / "state" / "commit-authorizations.jsonl"

AUTH_PHRASE = "authorize commit"

# How many newest entries the head walk may try when the walk from one runs into a parent the file
# does not hold yet: room for the few hook attachments a call could collect before its own line.
HEAD_CANDIDATES = 16

# Each git read is one `rev-parse`, a few milliseconds; the signal script stops the whole reader
# at 4.5 s.
GIT_SECONDS = 1.5

# Variables that would point git somewhere other than the directory the commit runs in. The
# agent's shell does not keep exported state between calls, so the commit sees none of them.
GIT_LOCATION_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                          "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE")

NO_REPOSITORY = {"repository": False}

# `<status>` values a task notification carries once its task has ended, measured over the 60
# newest transcripts on 2026-10-21: completed, failed, killed, stopped.
TASK_ENDED = {"completed", "failed", "killed", "stopped"}
BACKGROUND_RE = re.compile(r"running in background with ID: ([A-Za-z0-9_-]+)")
TASK_NOTIFICATION_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)

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


def parse_commit(command: str, cwd: str | None) -> str:
    """The absolute directory the commit runs in, or raise Refused."""
    check_shell_shape(command)
    try:
        tokens = shlex.split(command)
    except ValueError:
        raise Refused("the commit command could not be tokenized")
    if not tokens or tokens[0] != "git":
        raise Refused("the command does not start with `git`")
    directory, i = cwd, 1
    while i < len(tokens) and tokens[i] in ("-C", "-c"):
        if i + 1 >= len(tokens):
            raise Refused(f"`{tokens[i]}` has no value")
        if tokens[i] == "-C":
            # git resolves each `-C` against the one before it, as os.path.join does.
            target = tokens[i + 1]
            if not os.path.isabs(target) and directory is None:
                raise Refused("the event names no working directory to resolve `-C` against")
            directory = os.path.join(directory or "", target)
        i += 2
    if i >= len(tokens) or tokens[i] != "commit":
        raise Refused("only `git [-C <dir>] commit ...` can be authorized")
    if directory is None:
        raise Refused("the event names no working directory, so the repository is unknown")
    return os.path.abspath(directory)


# --- the repository a commit lands in ----------------------------------------------------------

def git_run(args: list[str], cwd: str) -> subprocess.CompletedProcess | None:
    env = {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_VARIABLES}
    env["LC_ALL"] = "C"
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=GIT_SECONDS, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def repo_state(directory: str) -> dict | None:
    """What a commit landing in `directory` changes, or None when that cannot be read.

    `{"head": <HEAD's commit, "" while unborn>, "reflog": <bytes in HEAD's reflog, -1 for none>}`,
    or `NO_REPOSITORY` when the directory does not exist or is in no repository, where git refuses
    to commit at all.
    """
    if not os.path.isdir(directory):
        return dict(NO_REPOSITORY)
    proc = git_run(["rev-parse", "--git-path", "logs/HEAD"], directory)
    if proc is None:
        return None
    if proc.returncode != 0:
        return dict(NO_REPOSITORY) if "not a git repository" in proc.stderr else None
    log = os.path.join(directory, proc.stdout.strip())  # an absolute path stays as it is
    head = git_run(["rev-parse", "-q", "--verify", "HEAD^{commit}"], directory)
    if head is None or head.returncode not in (0, 1):
        return None
    try:
        size = os.stat(log).st_size
    except FileNotFoundError:
        size = -1
    except OSError:
        return None
    return {"head": head.stdout.strip() if head.returncode == 0 else "", "reflog": size}


# --- the transcript ----------------------------------------------------------------------------

def message_text(entry: dict) -> str | None:
    content = (entry.get("message") or {}).get("content") if isinstance(entry.get("message"), dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
            for b in content):
        return "".join(b["text"] for b in content)
    return None


NO_PROMPT = "no message typed by the user precedes this tool call"


def walk(start: dict, by_uuid: dict[str, dict]) -> tuple[str, str, list[dict]] | None:
    """(uuid, text, chain) of the first genuine prompt back from `start`, the chain newest first.

    None when the walk reaches a parent the transcript does not hold yet. Raises Refused at a root
    or a compaction summary, where the conversation legitimately ends without one.
    """
    node, seen, chain = start, set(), []
    while True:
        uid = node.get("uuid")
        if uid in seen:
            raise Refused("the transcript's conversation chain loops, so it cannot be trusted")
        seen.add(uid)
        chain.append(node)
        text = shared.prompt_text(node)
        if text is not None and isinstance(uid, str):
            return uid, text, chain
        if node.get("type") == "user" and node.get("isCompactSummary"):
            raise Refused("no message typed by the user since the conversation was last compacted")
        parent = node.get("parentUuid")
        if not isinstance(parent, str):
            raise Refused(NO_PROMPT)
        node = by_uuid.get(parent)
        if node is None:
            return None


def locate(path: str, tool_use_id: str) -> tuple[str, str, list[dict]]:
    """(uuid, text, chain) of the last prompt the user typed before this tool call."""
    transcript = shared.Transcript(path)
    transcript.refresh()
    entries = transcript.entries()
    by_uuid: dict[str, dict] = {}
    for e in entries:
        uid = e.get("uuid")
        if isinstance(uid, str):
            if uid in by_uuid:
                raise Refused("the transcript repeats an entry uuid, so it cannot be trusted")
            by_uuid[uid] = e
    holders = [e for e in entries if shared.holds_tool_use(e, tool_use_id)]
    if len(holders) > 1:
        raise Refused("the transcript holds this tool call more than once, so it cannot be trusted")
    if holders:
        found = walk(holders[0], by_uuid)
        if found is None:
            raise Refused(NO_PROMPT)
        return found
    heads = [e for e in reversed(entries)
             if isinstance(e.get("uuid"), str) and not e.get("isSidechain")]
    for head in heads[:HEAD_CANDIDATES]:
        found = walk(head, by_uuid)
        if found is not None:
            return found
    raise Refused("the newest transcript entries do not link back to a message the user typed")


def not_phrase_reason(chain: list[dict]) -> str:
    for e in chain:
        origin = e.get("origin")
        if (e.get("type") == "user" and isinstance(origin, dict) and origin.get("kind") == "human"
                and e.get("promptSource") == "suggestion_accepted"
                and (message_text(e) or "").strip() == AUTH_PHRASE):
            return ("the user's `authorize commit` was an accepted prompt suggestion, text the "
                    "model wrote and the user only sent, and only a reply the user typed counts")
    return "the user's last message is not `authorize commit`"


def result_entry(chain: list[dict], tool_use_id: str) -> tuple[int, dict, dict] | None:
    """(position in `chain`, entry, block) of that call's tool_result, or None."""
    for i, e in enumerate(chain):
        if e.get("type") != "user":
            continue
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == tool_use_id:
                return i, e, b
    return None


def block_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def notification_texts(entry: dict) -> list[str]:
    origin = entry.get("origin")
    if entry.get("type") == "user" and isinstance(origin, dict) and origin.get("kind") == "task-notification":
        text = message_text(entry)
        return [text] if text else []
    attachment = entry.get("attachment")
    if (entry.get("type") == "attachment" and isinstance(attachment, dict)
            and attachment.get("type") == "queued_command"
            and attachment.get("commandMode") == "task-notification"
            and isinstance(attachment.get("prompt"), str)):
        return [attachment["prompt"]]
    return []


def task_ended(newer: list[dict], tool_use_id: str, task_id: str | None) -> bool:
    """Whether a notification in `newer` says that background task has ended."""
    for e in newer:
        for text in notification_texts(e):
            for block in TASK_NOTIFICATION_RE.findall(text):
                ours = (f"<tool-use-id>{tool_use_id}</tool-use-id>" in block
                        or (task_id is not None and f"<task-id>{task_id}</task-id>" in block))
                status = re.search(r"<status>([^<]*)</status>", block)
                if ours and status and status.group(1).strip() in TASK_ENDED:
                    return True
    return False


def holder_finished(chain: list[dict], tool_use_id: str) -> bool:
    """Whether that tool call has finished, read only from entries on this call's own chain.

    Finished: it has a result that is not an interrupt and not a background launch, or it was
    launched in the background and a later notification says the task ended. Anything else -- no
    result yet, an interrupt (the process may still be finishing), a background task with no
    notification -- is not known to have finished.
    """
    if not any(shared.holds_tool_use(e, tool_use_id) for e in chain):
        return False
    found = result_entry(chain, tool_use_id)
    if found is None:
        return False
    position, entry, block = found
    if entry.get("toolDenialKind") == "cancelled":
        return False
    meta = entry.get("toolUseResult")
    meta = meta if isinstance(meta, dict) else {}
    if meta.get("interrupted") is True:
        return False
    m = BACKGROUND_RE.search(block_text(block))
    task_id = meta.get("backgroundTaskId") if isinstance(meta.get("backgroundTaskId"), str) else None
    if m or task_id:
        return task_ended(chain[:position], tool_use_id, task_id or m.group(1))
    return True


# --- the record of admitted commits ------------------------------------------------------------

def records_for(lines, message_uuid: str) -> list[dict]:
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("message_uuid") == message_uuid:
            out.append(rec)
    return out


def landed(rec: dict) -> str | None:
    """Why the commit `rec` admitted counts as landed, or None while its repository is unchanged."""
    directory = rec.get("repo")
    if not isinstance(directory, str):
        return "that `authorize commit` was already spent on a commit"
    now = repo_state(directory)
    if now is None:
        return (f"that `authorize commit` paid for a commit in {directory}, which can no longer be "
                "read, so it counts as spent")
    if now != rec.get("state"):
        return (f"that `authorize commit` was already spent: HEAD in {directory} has moved since "
                "the commit it paid for was admitted")
    return None


def spent_reason(rec: dict, chain: list[dict]) -> str | None:
    """Why the message `rec` holds can pay for no more commits or lints, or None while it is live."""
    holder = rec.get("tool_use_id")
    if "state" not in rec:
        # Written before commits were judged by their repository: spent unless Claude Code refused
        # the holder before it ran (`shared.call_outcome` is False only then).
        if isinstance(holder, str) and shared.call_outcome(chain, holder) is False:
            return None
        return "that `authorize commit` was already spent on a commit"
    return landed(rec)


def take_over_refusal(rec: dict, chain: list[dict]) -> str | None:
    """Why another call may not take the message from `rec`'s holder, or None when it may."""
    reason = spent_reason(rec, chain)
    if reason is not None:
        return reason
    holder = rec.get("tool_use_id")
    if "state" in rec and not holder_finished(chain, str(holder)):
        return (f"that `authorize commit` is held by commit {holder}, which has not finished or "
                "was interrupted, so whether it lands is not known yet")
    return None


def live_reason(message_uuid: str, chain: list[dict]) -> str | None:
    """None while the message can still pay for a commit (lints may run), else why it cannot."""
    try:
        with open(state_file(), encoding="utf-8") as fh:
            records = records_for(fh, message_uuid)
    except OSError:
        return None
    return spent_reason(records[-1], chain) if records else None


def spend(message_uuid: str, tool_use_id: str, directory: str, state: dict,
          chain: list[dict]) -> str | None:
    """Record this commit against the message: None when it may go, else why it may not."""
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        records = records_for(fh, message_uuid)
        record = {"message_uuid": message_uuid, "tool_use_id": tool_use_id, "repo": directory,
                  "state": state, "at": time.time()}
        if records:
            holder = records[-1].get("tool_use_id")
            if holder == tool_use_id:
                return None  # the same call evaluated again, by a second hook
            if any(r.get("tool_use_id") == tool_use_id for r in records):
                return "that `authorize commit` has moved on from this tool call to a later one"
            refusal = take_over_refusal(records[-1], chain)
            if refusal is not None:
                return refusal
            record["replaces"] = holder
        fh.seek(0, os.SEEK_END)
        fh.write(json.dumps(record) + "\n")
        return None


# --- the decision ------------------------------------------------------------------------------

def decide(event: dict) -> tuple[str, str, str] | None:
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
        message_uuid, text, chain = locate(path, tool_use_id)
    except (Refused, shared.Refused) as r:
        return commit_denied, "DENIED", str(r)

    if text.strip() != AUTH_PHRASE:
        return commit_denied, "DENIED", not_phrase_reason(chain)

    previous = live_reason(message_uuid, chain)
    lint = "LIVE" if previous is None else "DENIED"
    if not is_commit:
        return "-", lint, message_uuid if previous is None else previous
    if previous is not None:
        return "DENIED", "DENIED", previous

    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and event.get("cwd") else None
    try:
        directory = parse_commit(command.strip(), cwd)
    except Refused as r:
        return "DENIED", lint, str(r)
    state = repo_state(directory)
    if state is None:
        return "DENIED", lint, (f"the repository at {directory} could not be read, so whether "
                                "the commit lands could not be checked")
    refusal = spend(message_uuid, tool_use_id, directory, state, chain)
    if refusal is not None:
        return "DENIED", "DENIED", refusal
    return "AUTHORIZED", lint, message_uuid


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    try:
        verdict = decide(event)
        if verdict is not None:
            emit(*verdict)
    except Exception as exc:  # noqa: BLE001 -- any failure must deny, and say so
        emit("DENIED", "DENIED", f"the authorization reader failed: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
