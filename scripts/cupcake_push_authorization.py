#!/usr/bin/env python3
"""Decide whether the pending Bash command is a push the user has authorized, for one push.

Read by `.cupcake/signals/push_authorization.sh`, consumed by
`.cupcake/policies/claude/git_block_any_push.rego`. User directive 2026-10-07, verbatim: "update the
git push blocking policy to give you an escape hatch if my last response is the same code word you
provide me, like "authorize <SHA>" so you can push if and only if that's what my message contains".

Input: the PreToolUse event cupcake writes to the signal's stdin (measured 2026-10-07 against
cupcake 0.5.2: it carries `transcript_path`, `tool_use_id`, `agent_id`, `cwd` and `tool_input`
through unchanged). Output: one tab-separated line, or nothing when the command does not mention a
push at all:

    `AUTHORIZED <branch> <tip sha> <message uuid>`
    `DENIED     <branch or -> <tip sha or -> <reason>`

The policy lifts its deny only on `AUTHORIZED`, so a crash, a timeout or empty output keeps every
push denied. Every check below fails closed.

The authorization is the text of the last genuine user prompt on the conversation chain that ends
at this tool call, and nothing else:

1. The pending tool call is located in the transcript by its `tool_use_id`, and the chain is walked
   backwards through `parentUuid` from there. Claude Code writes that link from its own memory, so
   a line appended to the transcript by anyone else is never on the chain. A transcript in which
   any `uuid` occurs twice is refused outright: that is the one way an appended line could stand in
   for an entry that is on the chain (none of the 40 newest transcripts in this project had one).
   The tool call's own line can land after this reader has started (see `LOCATE_DEADLINE_SECONDS`),
   so a missing line is looked for again in what gets appended, for a bounded time, and denies
   when it never arrives.
2. A genuine prompt is a `type: user` entry with `origin.kind == "human"` and `promptSource` of
   `typed` or `queued`, not `isMeta`, not `isCompactSummary`, not a sidechain, whose content is a
   string or text blocks only. Measured over every transcript in this project: tool results,
   task notifications (`origin.kind == "task-notification"`), peer messages, hook and
   system-reminder carriers, compaction summaries and slash-command caveats all fail that test.
   `suggestion_accepted` is refused on purpose: its text was written by the model and the user only
   pressed enter.
3. The walk stops at the first genuine prompt. Its whole text, trimmed, must be `authorize <sha>`
   with 7 to 40 hex digits. Anything else in the message denies.
4. The command must be exactly `git [-C <dir>] push [-u|--set-upstream] <remote> <branch>`: no
   shell operators, no other options, no refspec syntax (`+`, `:`), one branch, not main or master.
   The branch tip is read with `git rev-parse refs/heads/<branch>` in the target repository; the
   command text is never trusted for it. The tip must start with the authorized sha.
5. One authorization buys one push. The message uuid is recorded in
   `.cupcake/state/push-authorizations.jsonl` (gitignored) with the tool call that spent it. The
   same message with a different tool call is refused. The same tool call again is allowed, because
   more than one hook may evaluate the same event; it is still the same push.

What this cannot stop, stated plainly: the transcript is a file owned by the same user the agent's
shell runs as. Appending to it achieves nothing (point 1), and
`.cupcake/policies/claude/transcript_write_guard.rego` refuses the direct ways of rewriting it, but
an agent determined to rewrite the file in place by some other program could. Deleting the state
file re-arms a spent authorization; it cannot create one.
"""
from __future__ import annotations

import ctypes
import fcntl
import json
import os
import re
import select
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = REPO_ROOT / ".cupcake" / "state" / "push-authorizations.jsonl"

AUTH_RE = re.compile(r"authorize ([0-9a-fA-F]{7,40})")
PROTECTED_BRANCHES = {"main", "master", "HEAD"}
HUMAN_PROMPT_SOURCES = {"typed", "queued"}
SHELL_META = set(";&|<>()`$\n\r\\*?[]{}~!#")

# How long the locate step keeps watching the transcript for the pending tool call, counted from
# this reader's start. It re-reads when inotify reports a write, never on a timer. The budget this
# has to fit, measured 2026-10-14:
# cupcake 0.5.2 gives a signal it discovers in `.cupcake/signals/` 5 s (`default_timeout()` in
# cupcake-core's `engine/rulebook.rs`; a rulebook entry could change it, a discovered signal cannot;
# a fixture signal that answered after 4.6 s was read and one that answered after 5.4 s was dropped,
# which denies). `push_authorization.sh` stops this reader at 4.5 s, so that a reader still running
# when cupcake gives up is never left to record a spend for a push cupcake has already denied. A 3 s
# wait leaves 1.5 s of that 4.5 s for the rest, which after the line lands is one read of the
# appended bytes, the chain walk and the spend: under 0.2 s on an 18 MB transcript.
#
# Against the lag it has to cover: in a subagent transcript every tool_use line landed 0.10-0.11 s
# after its own `timestamp` (14 calls), while in the busy main session that was refused it had not
# landed when a reader that answered within 1.06 s looked for it (see `last_prompt`). 3 s is about
# thirty times the first and nearly three times the second.
LOCATE_DEADLINE_SECONDS = 3.0

# inotify(7) constants from <sys/inotify.h> (x86-64 Linux).
IN_MODIFY = 0x00000002
IN_CLOSE_NOWRITE = 0x00000010
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000


def emit(verdict: str, branch: str | None, tip: str | None, detail: str) -> None:
    detail = detail.replace("\t", " ").replace("\n", " ")
    sys.stdout.write(f"{verdict}\t{branch or '-'}\t{tip or '-'}\t{detail}")


def git(args: list[str], cwd: str) -> str | None:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


# --- the command -------------------------------------------------------------------------------

class Refused(Exception):
    def __init__(self, reason: str, branch: str | None = None, tip: str | None = None):
        super().__init__(reason)
        self.reason, self.branch, self.tip = reason, branch, tip


def parse_push(command: str, cwd: str) -> tuple[str, str, str]:
    """(repo dir, remote, branch) for the one authorizable push shape, or raise Refused."""
    if any(ch in SHELL_META for ch in command):
        raise Refused("the command carries shell syntax; run the push as a single plain "
                      "`git push <remote> <branch>`")
    try:
        tokens = shlex.split(command)
    except ValueError:
        raise Refused("the command could not be tokenized")
    if not tokens or tokens[0] != "git":
        raise Refused("the command does not start with `git`")
    i, repo = 1, cwd
    if tokens[i:i + 1] == ["-C"] and len(tokens) > i + 1:
        repo = tokens[i + 1] if os.path.isabs(tokens[i + 1]) else os.path.join(cwd, tokens[i + 1])
        i += 2
    if tokens[i:i + 1] != ["push"]:
        raise Refused("only `git [-C <dir>] push ...` can be authorized")
    rest = [t for t in tokens[i + 1:] if t not in ("-u", "--set-upstream")]
    if any(t.startswith("-") for t in rest):
        raise Refused("push options other than -u/--set-upstream (force, delete, tags, mirror, "
                      "all, no-verify, ...) are never authorizable")
    if len(rest) != 2:
        raise Refused("name exactly one remote and one branch: `git push <remote> <branch>`")
    remote, branch = rest
    if branch.startswith("+") or ":" in branch or branch.startswith("refs/"):
        raise Refused("refspec syntax is never authorizable; name the branch alone", branch)
    if branch in PROTECTED_BRANCHES:
        raise Refused(f"`{branch}` is never pushed by the agent", branch)
    if git(["check-ref-format", "--branch", branch], repo) is None:
        raise Refused(f"`{branch}` is not a valid branch name", branch)
    remotes = git(["remote"], repo)
    if remotes is None or remote not in remotes.split():
        raise Refused(f"`{remote}` is not a configured remote of {repo}", branch)
    return repo, remote, branch


# --- the transcript ----------------------------------------------------------------------------

def parse_line(raw: bytes) -> dict | None:
    line = raw.decode("utf-8", errors="replace").strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


class Transcript:
    """The transcript's entries, kept current by reading only the bytes appended since last time.

    Lines are split the way a text-mode read splits them (`\\r\\n`, `\\r` and `\\n` all end a line),
    so the entries are the ones a single whole-file read would have produced. A trailing line with
    no newline yet counts when it already parses, as it would in a whole-file read, and is read
    again once more bytes arrive. A file that shrank or was replaced is read again from its start.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.identity: tuple[int, int] | None = None
        self.offset = 0
        self.tail = b""
        self.complete: list[dict] = []

    def refresh(self) -> None:
        with open(self.path, "rb") as fh:
            st = os.fstat(fh.fileno())
            identity = (st.st_dev, st.st_ino)
            if identity != self.identity or st.st_size < self.offset:
                self.identity, self.offset, self.tail, self.complete = identity, 0, b"", []
            fh.seek(self.offset)
            data = fh.read()
        self.offset += len(data)
        data = (self.tail + data).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        *lines, self.tail = data.split(b"\n")
        self.complete.extend(e for e in map(parse_line, lines) if e is not None)

    def entries(self) -> list[dict]:
        last = parse_line(self.tail)
        return self.complete + ([last] if last is not None else [])


class FileEvents:
    """inotify on one file, through libc: `wait(seconds)` is true once an event in `mask` arrives.

    Raises `OSError` when inotify is unavailable. Add the watch before the read it guards, so a
    write landing between the two still wakes the next `wait`.
    """

    def __init__(self, path: str, mask: int) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        self.fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        if libc.inotify_add_watch(self.fd, os.fsencode(path), mask) < 0:
            err = ctypes.get_errno()
            os.close(self.fd)
            raise OSError(err, "inotify_add_watch")

    def wait(self, seconds: float) -> bool:
        ready, _, _ = select.select([self.fd], [], [], max(0.0, seconds))
        if not ready:
            return False
        try:
            os.read(self.fd, 65536)  # drain, so the next wait blocks until a new event
        except BlockingIOError:
            pass
        return True

    def close(self) -> None:
        os.close(self.fd)

    def __enter__(self) -> FileEvents:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def holds_tool_use(entry: dict, tool_use_id: str) -> bool:
    if entry.get("type") != "assistant":
        return False
    content = (entry.get("message") or {}).get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") == tool_use_id
        for b in content
    )


def prompt_text(entry: dict) -> str | None:
    """The text of a genuine user prompt, or None when the entry is anything else."""
    if entry.get("type") != "user":
        return None
    origin = entry.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "human":
        return None
    if entry.get("promptSource") not in HUMAN_PROMPT_SOURCES:
        return None
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content and all(
        isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        for b in content
    ):
        return "".join(b["text"] for b in content)
    return None


def last_prompt(path: str, tool_use_id: str, deadline: float) -> tuple[str, str]:
    """(message uuid, text) of the last genuine prompt on the chain ending at this tool call."""
    # Claude Code does not write the tool_use line before it starts the PreToolUse hook; the hook
    # and the write race. Measured 2026-10-14 on Claude Code 2.1.281 by recording when each line
    # of a subagent transcript landed: tool_use lines landed 0.10-0.11 s after their `timestamp`,
    # and the hooks for the same calls had started within 10 ms of it, so the hook is already
    # running when the line arrives. The main session can be later still. On 2026-10-14 two
    # consecutive authorized pushes were refused because this reader did not find the line; their
    # refusals are timestamped 1.06 s and 1.36 s after the call they refused, so the line was
    # still missing when the reader looked, some time before that. A third call was found, and
    # spent, 1.12 s after its own. The 2026-10-07 reading this comment used to cite (the line on
    # disk about 1.1 s before the hook finished) was one sample of that race, not an ordering
    # Claude Code promises.
    #
    # So a missing line is looked for again in what gets appended, each time inotify reports a
    # write, until `deadline`. Without inotify there is one read and no wait, as before. Waiting
    # changes nothing else: the chain is still walked from the line Claude Code wrote, and a line
    # that never arrives still denies.
    try:
        writes: FileEvents | None = FileEvents(path, IN_MODIFY)
    except OSError:
        writes = None
    try:
        transcript = Transcript(path)
        transcript.refresh()
        entries = transcript.entries()
        start = next((e for e in reversed(entries) if holds_tool_use(e, tool_use_id)), None)
        while (start is None and writes is not None and time.monotonic() < deadline
               and writes.wait(deadline - time.monotonic())):
            transcript.refresh()
            entries = transcript.entries()
            start = next((e for e in reversed(entries) if holds_tool_use(e, tool_use_id)), None)
    finally:
        if writes is not None:
            writes.close()
    if start is None:
        raise Refused(f"this tool call was still not in the transcript {LOCATE_DEADLINE_SECONDS:g} s "
                      "after the check began, so its conversation cannot be read; run the same "
                      "push again")
    by_uuid: dict[str, dict] = {}
    for e in entries:
        uid = e.get("uuid")
        if isinstance(uid, str):
            if uid in by_uuid:
                raise Refused("the transcript repeats an entry uuid, so it cannot be trusted")
            by_uuid[uid] = e
    node, seen = start, set()
    while node is not None:
        uid = node.get("uuid")
        if uid in seen:
            break
        seen.add(uid)
        text = prompt_text(node)
        if text is not None:
            return uid, text
        if node.get("type") == "user" and node.get("isCompactSummary"):
            break
        parent = node.get("parentUuid")
        node = by_uuid.get(parent) if isinstance(parent, str) else None
    raise Refused("no message typed by the user precedes this tool call")


# --- one push per authorization ----------------------------------------------------------------

def spend(message_uuid: str, tool_use_id: str, branch: str, tip: str) -> bool:
    """Record the spend; False when this message already paid for a different tool call."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "a+", encoding="utf-8") as fh:
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
        fh.write(json.dumps({
            "message_uuid": message_uuid, "tool_use_id": tool_use_id,
            "branch": branch, "tip": tip, "at": time.time(),
        }) + "\n")
        return True


# --- the decision ------------------------------------------------------------------------------

def branch_tip(repo: str, branch: str) -> str | None:
    tip = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"], repo)
    return tip if tip and re.fullmatch(r"[0-9a-f]{40}", tip) else None


def decide(event: dict, started: float) -> None:
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    command = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
    if "push" not in command:
        return
    if event.get("agent_id"):
        raise Refused("only the main session can push on the user's authorization, never a subagent")
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else os.getcwd()
    repo, _remote, branch = parse_push(command.strip(), cwd)
    tip = branch_tip(repo, branch)
    if tip is None:
        raise Refused(f"no local branch `{branch}` in {repo}", branch)
    path, tool_use_id = event.get("transcript_path"), event.get("tool_use_id")
    if not isinstance(path, str) or not os.path.isfile(path):
        raise Refused("the session transcript is not readable", branch, tip)
    if not isinstance(tool_use_id, str) or not tool_use_id:
        raise Refused("the event names no tool call", branch, tip)
    try:
        message_uuid, text = last_prompt(path, tool_use_id, started + LOCATE_DEADLINE_SECONDS)
    except Refused as r:
        raise Refused(r.reason, branch, tip)
    # Read the tip again: the transcript step may have waited seconds, and the tip compared with
    # the authorization must be the one the push is about to send, not the one from before.
    tip = branch_tip(repo, branch)
    if tip is None:
        raise Refused(f"no local branch `{branch}` in {repo}", branch)
    m = AUTH_RE.fullmatch(text.strip())
    if m is None:
        raise Refused("the user's last message is not an authorization", branch, tip)
    if not tip.startswith(m.group(1).lower()):
        raise Refused(f"the user authorized {m.group(1)}, which is not the tip of `{branch}`",
                      branch, tip)
    if not spend(message_uuid, tool_use_id, branch, tip):
        raise Refused("that authorization was already spent on another push", branch, tip)
    emit("AUTHORIZED", branch, tip, message_uuid)


def main() -> int:
    started = time.monotonic()
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    try:
        decide(event, started)
    except Refused as r:
        emit("DENIED", r.branch, r.tip, r.reason)
    except Exception as exc:  # noqa: BLE001 -- any failure must deny, and say so
        emit("DENIED", None, None, f"the authorization reader failed: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
