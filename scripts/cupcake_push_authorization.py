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
   any `uuid` occurs twice is refused outright: that is one way an appended line could stand in
   for an entry that is on the chain (none of the 40 newest transcripts in this project had one).
   The other way is a copy of the pending tool call itself, appended after the real one by
   something that read its id there and hung off a forged prompt; a transcript in which a second
   entry holds the pending `tool_use_id` is refused too (no id repeats among the 24,298 tool calls
   in the 80 newest transcripts and subagent transcripts in this project, measured 2026-10-14).
   The tool call's own line can land after this reader has started (see `LOCATE_DEADLINE_SECONDS`),
   so a missing line is looked for again in what gets appended, for a bounded time, and denies
   when it never arrives.
2. A genuine prompt is a `type: user` entry with `origin.kind == "human"` and `promptSource` of
   `typed` or `queued`, not `isMeta`, not `isCompactSummary`, not a sidechain, whose content is a
   string or blocks. Measured over every transcript in this project: tool results,
   task notifications (`origin.kind == "task-notification"`), peer messages, hook and
   system-reminder carriers, compaction summaries and slash-command caveats all fail that test.
   `suggestion_accepted` is refused on purpose: its text was written by the model and the user only
   pressed enter. A message the user types while a turn is running is not written as a `type:
   user` entry at all: it reaches the chain as an `attachment` whose `attachment` is
   `{"type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"}, ...}`, and
   that is a genuine prompt too, with `attachment.prompt` as its text, when neither the entry nor
   the attachment is `isMeta` and the entry is not a sidechain (`queued_prompt_text`). Queue
   operations and `last-prompt` entries never count. A block that is not text (a pasted image)
   still makes the message the user's last one, but no phrase can match it.
3. The walk stops at the first genuine prompt. Its whole text, trimmed, must be `authorize <sha>`
   with 7 to 40 hex digits. Anything else in the message denies.
4. The command must be exactly `git [-C <dir>] push [-u|--set-upstream] <remote> <branch>`: no
   shell operators, no other options, no refspec syntax (`+`, `:`), one branch, not main or master.
   The branch tip is read with `git rev-parse refs/heads/<branch>` in the target repository; the
   command text is never trusted for it. The tip must start with the authorized sha.
5. One authorization buys one push that ran. The message uuid is recorded in
   `.cupcake/state/push-authorizations.jsonl` (gitignored) with the tool call holding it. The same
   tool call again is allowed, because more than one hook may evaluate the same event; it is still
   the same push. A different tool call is refused, unless the chain shows Claude Code refused the
   holder before it ran (another policy denied it, or the user rejected it), or the pre-push hook
   recorded a refund for it (it refused the push for a precondition such as a running game, before
   any gate judged the content; `scripts/er-pre-push-clear.py`): then the new call takes the
   authorization over and the record says which call it replaces. See `spend`.

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
import mmap
import os
import re
import select
import shlex
import subprocess
import sys
import time
from collections import Counter
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
# wait leaves 1.5 s of that 4.5 s for the rest. The whole decision on the 18 MB transcript that was
# refused takes 0.25-0.3 s, most of it the first whole read, which comes before the wait; once the
# line lands there is one read of the appended bytes, the chain walk and the spend. (On the 184 MB
# transcript of 2026-11-26 the decision took 0.24 s idle, against 1.40 s when every line was
# parsed; `TranscriptIndex` says what is read.)
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


# --- reading a large transcript without parsing all of it -------------------------------------
#
# The readers used to parse every line of the transcript, and that is what timed out: on
# 2026-11-26 the main session's transcript was 183,567,159 bytes, the lint guard refused twice
# with "the authorization reader gave no answer" under a load average of 25, and the same reader
# measured on that file at load 5.7 took 1.40 s of wall at 99% CPU, 0.90 s of it `json.loads` over
# 73,403 lines and 0.19 s splitting them. Byte scans of the same file cost 0.02-0.12 s each.
#
# So the file is read (or mapped) once as bytes, and only the lines the decision needs are parsed:
# the lines that can hold the pending tool call, the lines on the chain, and the newest few. Three
# byte scans cover the whole file: `\r` (0.03 s on that file), the literal tool call id (0.02 s)
# and `UUID_KEY_RE` (0.09 s). The scan for `\u` escapes (0.07-0.15 s over the whole file) covers
# only the tail that holds the chain. Every check the whole parse made still holds against an
# appended line, because:
#
# * Claude Code writes a uuid and a tool call id literally: they are ASCII letters, digits, `-`
#   and `_`, which JSON never escapes. The only other spelling that decodes to one of them uses
#   `\uXXXX`, which only a line holding the two bytes `\u` can carry (741 of the 73,403 lines
#   measured). A short escape such as `\/` or `\"` decodes to none of those characters.
# * A `"uuid"` key match in a line that is valid JSON is always a real key, because a quote
#   inside a string is always escaped. So every line Claude Code wrote with uuid X is among the
#   `UUID_KEY_RE` matches with value X (measured on the same file: 40,922 matches, 40,922 entries
#   with a uuid, no line with two), and a copy anyone else wrote is either a match too or a `\u`
#   line.
# * A copy of an entry, or of the pending tool call, can only be appended after the original:
#   both ids are random, and nobody knows one before Claude Code has written it. So a copy of an
#   entry the walk used is either a literal match (seen wherever it is) or a `\u` line after that
#   entry, and the `\u` scan covers everything from the oldest entry the walk used to the end
#   (`TranscriptIndex.covers`). A uuid with two owners refuses, as before. What the narrower scan
#   no longer sees is a `\u`-spelled line older than every entry the walk used. It cannot be a
#   copy of one of them (a copy comes after its original), so it changes no walk; the one thing
#   it could have done is stand in for a parent Claude Code has not written yet, which a whole
#   parse would have followed and this walk stops at instead.
UUID_KEY_RE = re.compile(rb'"uuid"[ \t]*:[ \t]*"([^"\\\n]*(?:\\.[^"\\\n]*)*)"')
ESCAPE = b"\\u"
TOOL_USE_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
REPEATED_UUID = "the transcript repeats an entry uuid, so it cannot be trusted"

# How much of the transcript's end the `\u` scan covers before any walk: the chain the readers
# walk is the conversation since the user's last message, and in the 183 MB transcript of
# 2026-11-26 that was the last 174 entries. A walk that reaches further back extends it.
TAIL_BYTES = 16 * 1024 * 1024


def map_file(path: str):
    """The file's bytes, mapped rather than copied (an empty file is `b""`).

    For a reader that looks once. A mapping keeps the file open until it is closed, so a reader
    that waits for inotify close events on its own reads (`prompt_and_chain`) reads instead.
    """
    with open(path, "rb") as fh:
        try:
            return mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # an empty file cannot be mapped
            return b""


def plain_tool_use_id(tool_use_id: str) -> bytes:
    if not TOOL_USE_ID_RE.fullmatch(tool_use_id):
        raise Refused("the event's tool call id is not one Claude Code writes")
    return tool_use_id.encode("ascii")


def _json_string(raw: bytes) -> str | None:
    text = raw.decode("utf-8", errors="replace")
    if "\\" not in text:
        return text
    try:
        value = json.loads('"' + text + '"')
    except ValueError:
        return None  # only an invalid line holds an undecodable string, and it is no entry
    return value if isinstance(value, str) else None


class TranscriptIndex:
    """One snapshot of the transcript that parses a line only when a question needs it.

    The entries it gives are the ones `Transcript.entries()` gives for the same bytes: a line is a
    run between `\\n`s of the normalized bytes, and the last one counts when it parses. The `\\u`
    lines are known from `self.region` to the end; a caller calls `check_unique_uuids`, takes the
    holders, walks with `get`, then asks `covers(chain)`, and walks again when that says so (the
    comment above `UUID_KEY_RE` says why that is enough).
    """

    def __init__(self, data, tail_bytes: int = TAIL_BYTES, carriage: bool | None = None,
                 plain_hits: dict[str, list[int]] | None = None) -> None:
        """`carriage` and `plain_hits` pass on what a `Snapshot` already knows about `data`:
        whether it holds a `\\r`, and where each tool call id it looked for occurs."""
        if carriage is None:
            carriage = data.find(b"\r") >= 0
        if carriage:
            data = bytes(data).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            plain_hits = None  # positions in the bytes before the line endings changed
        self.plain_hits = plain_hits or {}
        self.data = data
        self.size = len(data)
        self.parsed: dict[int, dict | None] = {}
        self.start_of: dict[int, int] = {}  # id(entry) -> its line's start
        self.region = self.size
        self.escaped: list[int] = []
        self.escaped_owners: dict[str, list[int]] = {}
        self.asked: dict[str, int | None] = {}  # uuid `get` was asked for -> the line it gave
        self.asked_tools: dict[str, set[int]] = {}  # tool call id -> the lines `holders` read
        self.stale = False  # `extend` found a line one of those lookups did not see
        self.keys: set[str] | None = None  # the value of every plain `"uuid"` key in the file
        self.repeated: set[str] = set()  # values found under more than one such key
        self.spelled: dict[str, list[bytes]] = {}  # raw spellings of values that are not plain
        self.extend(max(0, self.size - tail_bytes))

    def bounds(self, pos: int) -> tuple[int, int]:
        start = self.data.rfind(b"\n", 0, pos) + 1
        end = self.data.find(b"\n", pos)
        return start, self.size if end < 0 else end

    def entry(self, start: int) -> dict | None:
        if start not in self.parsed:
            _, end = self.bounds(start)
            entry = parse_line(self.data[start:end])
            self.parsed[start] = entry
            if entry is not None:
                self.start_of[id(entry)] = start
        return self.parsed[start]

    def extend(self, pos: int) -> None:
        """Scan for `\\u` lines from the line holding `pos` up to where the scan already reached."""
        start = self.bounds(pos)[0] if pos < self.size else self.size
        if start >= self.region:
            return
        found, at = [], start
        while (i := self.data.find(ESCAPE, at, self.region)) >= 0:
            line, end = self.bounds(i)
            found.append(line)
            at = end + 1
        for line in found:
            e = self.entry(line)
            if e is None:
                continue
            uid = e.get("uuid")
            if isinstance(uid, str):
                self.escaped_owners.setdefault(uid, []).append(line)
                if uid in self.asked and self.asked[uid] != line:
                    self.stale = True
            if any(line not in seen and holds_tool_use(e, t)
                   for t, seen in self.asked_tools.items()):
                self.stale = True
        self.escaped = found + self.escaped
        self.region = start

    def covers(self, chain: list[dict]) -> bool:
        """Whether the answer walked to `chain` stands.

        True when every entry of `chain` lies in the scanned region. Otherwise the region is
        extended back to the oldest of them; True again when that found no line any lookup made
        so far would have used (the uuid check is run once more over what it did find), and False
        when it found one, so that the caller walks again.
        """
        oldest = min(self.start_of[id(e)] for e in chain)
        if oldest >= self.region:
            return True
        self.stale = False
        self.extend(oldest)
        if self.stale:
            return False
        self.check_unique_uuids()
        return True

    def holders(self, tool_use_id: str) -> list[dict]:
        """Every entry holding that tool call, in file order."""
        needle, starts = plain_tool_use_id(tool_use_id), set(self.escaped)
        if tool_use_id in self.plain_hits:
            starts.update(self.bounds(i)[0] for i in self.plain_hits[tool_use_id])
        else:
            pos = 0
            while (i := self.data.find(needle, pos)) >= 0:
                start, end = self.bounds(i)
                starts.add(start)
                pos = end + 1
        self.asked_tools[tool_use_id] = starts
        return [e for s in sorted(starts)
                if (e := self.entry(s)) is not None and holds_tool_use(e, tool_use_id)]

    def _index_keys(self) -> set[str]:
        """Every plain `"uuid"` key's value, from one C-level scan (no per-match Python)."""
        if self.keys is None:
            raws = UUID_KEY_RE.findall(self.data)
            joined = b"".join(raws)
            if joined.isascii() and b"\\" not in joined:
                values = [r.decode("ascii") for r in raws]
            else:
                values = []
                for raw in raws:
                    value = _json_string(raw)
                    if value is None:
                        continue
                    values.append(value)
                    if raw != value.encode("utf-8", "surrogatepass"):
                        self.spelled.setdefault(value, []).append(raw)
            self.keys = set(values)
            if len(self.keys) != len(values):
                self.repeated = {v for v, n in Counter(values).items() if n > 1}
        return self.keys

    def _owns(self, start: int, uid: str) -> bool:
        e = self.entry(start)
        return e is not None and e.get("uuid") == uid

    def _needles(self, uid: str) -> list[bytes]:
        try:
            plain = [uid.encode("utf-8")]
        except UnicodeEncodeError:
            plain = []
        return plain + [r for r in dict.fromkeys(self.spelled.get(uid, ())) if r not in plain]

    def _plain_owner(self, uid: str, near: int, skip: int | None = None) -> int | None:
        """A line other than `skip` whose entry has uuid `uid`, looked for among the lines that
        spell it, back from `near` and then forward from it. None when there is none.

        Claude Code writes a parent before its child (no entry of the 2026-11-26 file came before
        its parent, and the furthest parent was 355,302 bytes back), so from the child's line
        this reads a few lines, not the file.
        """
        for needle in self._needles(uid):
            end = near
            while (i := self.data.rfind(needle, 0, end)) >= 0:
                start = self.bounds(i)[0]
                if start != skip and self._owns(start, uid):
                    return start
                end = start
            pos = near
            while (i := self.data.find(needle, pos)) >= 0:
                start, stop = self.bounds(i)
                if start != skip and self._owns(start, uid):
                    return start
                pos = stop + 1
        return None

    def _owners_of(self, uids: set[str]) -> dict[str, set[int]]:
        """Every owner line of each of `uids`: one more scan, made only when a key repeats."""
        out = {u: set(self.escaped_owners.get(u, ())) for u in uids}
        for m in UUID_KEY_RE.finditer(self.data):
            value = _json_string(m.group(1))
            if value in out:
                out[value].add(self.bounds(m.start())[0])
        return {u: {s for s in starts if self._owns(s, u)} for u, starts in out.items()}

    def check_unique_uuids(self) -> None:
        """Refuse a transcript in which an entry uuid occurs twice."""
        keys = self._index_keys()
        if self.repeated and any(len(o) > 1 for o in self._owners_of(self.repeated).values()):
            raise Refused(REPEATED_UUID)
        for uid, lines in self.escaped_owners.items():
            if len(lines) > 1:
                raise Refused(REPEATED_UUID)
            if uid not in keys or uid in self.repeated:
                continue
            # The file spells this uuid's key plainly exactly once. When that is not in this line,
            # the line holding it may be a second owner.
            line = lines[0]
            text = self.data[line:self.bounds(line)[1]]
            here = {_json_string(r) for r in UUID_KEY_RE.findall(text)}
            if uid not in here and self._plain_owner(uid, line, skip=line) is not None:
                raise Refused(REPEATED_UUID)

    def get(self, uid: object, child: dict | None = None) -> dict | None:
        """The entry with that uuid, looked for near `child`'s line; `check_unique_uuids` first."""
        if not isinstance(uid, str):
            return None
        if uid in self.escaped_owners:
            start = self.escaped_owners[uid][0]
        elif uid in self._index_keys():
            near = self.start_of.get(id(child), self.size) if child is not None else self.size
            start = self._plain_owner(uid, near)
        else:
            start = None
        self.asked[uid] = start
        return self.entry(start) if start is not None else None

    def newest(self, count: int) -> list[dict]:
        """The newest `count` entries with a uuid that are not on a sidechain, newest first."""
        out, end = [], len(self.data)
        while len(out) < count:
            start = self.data.rfind(b"\n", 0, end) + 1
            e = self.entry(start)
            if e is not None and isinstance(e.get("uuid"), str) and not e.get("isSidechain"):
                out.append(e)
            if start == 0:
                break
            end = start - 1
        return out


class Snapshot:
    """The transcript's bytes, kept current by reading only what was appended since last time.

    One `refresh` is one open and close of the file, as `Transcript.refresh` was. A file that
    shrank or was replaced is read again from its start.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.identity: tuple[int, int] | None = None
        self.data = b""
        self.carriage = False
        self.scanned = 0
        self.hits: list[int] = []

    def refresh(self) -> None:
        with open(self.path, "rb") as fh:
            st = os.fstat(fh.fileno())
            identity = (st.st_dev, st.st_ino)
            if identity != self.identity or st.st_size < len(self.data):
                self.identity, self.data, self.carriage = identity, b"", False
                self.scanned, self.hits = 0, []
            fh.seek(len(self.data))
            more = fh.read()
        if more:
            self.data += more
            self.carriage = self.carriage or b"\r" in more

    def line(self, pos: int) -> bytes:
        """The line holding byte `pos`, with `\\r` ending a line too when the file has one."""
        ends = (b"\n", b"\r") if self.carriage else (b"\n",)
        start = max(self.data.rfind(e, 0, pos) for e in ends) + 1
        stops = [i for i in (self.data.find(e, pos) for e in ends) if i >= 0]
        return self.data[start:min(stops) if stops else len(self.data)]

    def holds(self, tool_use_id: str) -> bool:
        """Whether a line holding that tool call, spelled plainly, is on disk now."""
        needle = plain_tool_use_id(tool_use_id)
        pos = max(0, self.scanned - len(needle) + 1)
        while (i := self.data.find(needle, pos)) >= 0:
            self.hits.append(i)
            pos = i + 1
        self.scanned = len(self.data)
        for i in self.hits:
            entry = parse_line(self.line(i))
            if entry is not None and holds_tool_use(entry, tool_use_id):
                return True
        return False


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
    """The text of a genuine user prompt, or None when the entry is anything else.

    The user's own typing reaches the transcript in two shapes (point 2 of the module docstring):
    a `type: user` entry, and a `queued_command` attachment for a message typed while a turn was
    running (`queued_prompt_text`).
    """
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain"):
        return None
    if entry.get("type") == "attachment":
        return queued_prompt_text(entry.get("attachment"))
    if entry.get("type") != "user":
        return None
    origin = entry.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "human":
        return None
    if entry.get("promptSource") not in HUMAN_PROMPT_SOURCES:
        return None
    message = entry.get("message")
    return typed_text(message.get("content") if isinstance(message, dict) else None)


def queued_prompt_text(attachment: object) -> str | None:
    """The text of a message the user typed while a turn was running, or None.

    Claude Code writes no `type: user` entry for such a message. It logs a `queue-operation`
    `enqueue` when the user sends it and a `remove` when the running turn takes it, then puts it on
    the chain as an attachment `{"type": "queued_command", "commandMode": "prompt", "origin":
    {"kind": "human"}, "prompt": <text>}`. On 2026-11-08 (session 5f9be448) the user's
    `authorize 8bf1941e` reached the transcript only that way, and the push it paid for was refused
    against the older `authorize bd7bd1eb` before it.

    Only that attachment counts. A queue operation is off the chain, and an enqueued message can be
    removed without being delivered; the `last-prompt` entry written after it still named the older
    reply. The same attachment type carries other senders, told apart by these fields (measured
    over the 200 newest transcripts and subagent transcripts in this project): peer agents'
    messages are `commandMode: prompt` with `origin.kind == "peer"`, the coordinator's have
    `isMeta` and `origin.kind == "coordinator"`, and task notifications are `commandMode:
    task-notification`. All 54 with `origin.kind == "human"` were the user's typing.
    """
    if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
        return None
    if attachment.get("commandMode") != "prompt" or attachment.get("isMeta"):
        return None
    origin = attachment.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "human":
        return None
    return typed_text(attachment.get("prompt"))


# Stands in for a block of a typed message that is not text, such as a pasted image (7 queued
# messages in this project's transcripts carry one). The message still ends the walk as the user's
# last one, and with this in its text no reply phrase can match it.
NON_TEXT_BLOCK = "\n[a block that is not text]"


def typed_text(content: object) -> str | None:
    """A typed message's text: a string, or its blocks' text joined. None for anything else."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or not content:
        return None
    return "".join(b["text"] if isinstance(b, dict) and b.get("type") == "text"
                   and isinstance(b.get("text"), str) else NON_TEXT_BLOCK for b in content)


def last_prompt(path: str, tool_use_id: str, deadline: float) -> tuple[str, str]:
    """(message uuid, text) of the last genuine prompt on the chain ending at this tool call."""
    message_uuid, text, _chain = prompt_and_chain(path, tool_use_id, deadline)
    return message_uuid, text


def prompt_and_chain(path: str, tool_use_id: str, deadline: float) -> tuple[str, str, list[dict]]:
    """`last_prompt`, plus every entry on the chain from this tool call back to that prompt."""
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
    #
    # The wait looks for the id spelled plainly, which is how Claude Code writes it; it only
    # decides when to stop waiting. Whatever the wait saw, the holders are then counted over the
    # whole snapshot, every spelling included (`TranscriptIndex.holders`).
    plain_tool_use_id(tool_use_id)
    try:
        writes: FileEvents | None = FileEvents(path, IN_MODIFY)
    except OSError:
        writes = None
    try:
        snapshot = Snapshot(path)
        snapshot.refresh()
        while (not snapshot.holds(tool_use_id) and writes is not None
               and time.monotonic() < deadline and writes.wait(deadline - time.monotonic())):
            snapshot.refresh()
    finally:
        if writes is not None:
            writes.close()
    index = TranscriptIndex(snapshot.data, carriage=snapshot.carriage,
                            plain_hits={tool_use_id: snapshot.hits})
    while True:
        found = chain_in(index, tool_use_id)
        if index.covers(found[2]):
            return found


def chain_in(by_uuid: TranscriptIndex, tool_use_id: str) -> tuple[str, str, list[dict]]:
    """`prompt_and_chain`'s answer from one snapshot, read as far as the index has scanned."""
    holders = by_uuid.holders(tool_use_id)
    # The id is unknown to anyone until Claude Code writes it, so the first entry holding it is
    # Claude Code's. A second one can only be a copy appended by whoever read the first, and if it
    # were taken as the start it would carry its own forged parent onto the chain.
    if len(holders) > 1:
        raise Refused("the transcript holds this tool call more than once, so it cannot be trusted")
    by_uuid.check_unique_uuids()
    if holders:
        found = walk_back(holders[0], by_uuid)
        if found is None:
            raise Refused(NO_PROMPT)
        return found
    # In the main session the line is not on disk at all while PreToolUse hooks run: Claude Code
    # writes it after they return (bd er-effects-rs-hu4w; on 2026-10-22, session e75d8996, two
    # authorized pushes of `feat/asset-geometry-tooling` were refused here after the full wait).
    # What is on disk then is the conversation up to this call, which descends from its newest
    # entry, so the chain is walked back from there -- the same fallback
    # `cupcake_commit_authorization.locate` takes. An entry whose parent is not written yet is
    # stepped over, up to `HEAD_CANDIDATES` of them.
    for head in by_uuid.newest(HEAD_CANDIDATES):
        found = walk_back(head, by_uuid)
        if found is not None:
            return found
    raise Refused("this tool call is not in the transcript, and its newest entries do not link back "
                  "to a message the user typed")


NO_PROMPT = "no message typed by the user precedes this tool call"

# How many newest entries the fallback walk may try when the walk from one runs into a parent the
# file does not hold yet: room for the few hook attachments a call could collect before its line.
HEAD_CANDIDATES = 16


def walk_back(start: dict, by_uuid) -> tuple[str, str, list[dict]] | None:
    """(uuid, text, chain) of the first genuine prompt back from `start`, the chain newest first.

    None when the walk reaches a parent the transcript does not hold yet. Raises Refused at a root,
    a loop or a compaction summary, where the conversation ends without one.
    """
    node, seen, chain = start, set(), []
    while True:
        uid = node.get("uuid")
        if uid in seen:
            raise Refused("the transcript's conversation chain loops, so it cannot be trusted")
        seen.add(uid)
        chain.append(node)
        text = prompt_text(node)
        if text is not None and isinstance(uid, str):
            return uid, text, chain
        if node.get("type") == "user" and node.get("isCompactSummary"):
            raise Refused(NO_PROMPT)
        parent = node.get("parentUuid")
        if not isinstance(parent, str):
            raise Refused(NO_PROMPT)
        node = by_uuid.get(parent, node)
        if node is None:
            return None


# --- one push per authorization ----------------------------------------------------------------

# The `toolDenialKind` values Claude Code writes on the tool_result of a call it refused before
# running it. Measured 2026-10-20 over the 30 newest transcripts in this project (553 refusals):
# `permission-rule` (a PreToolUse hook or a permission rule denied it -- 552 of them begin
# `PreToolUse:<tool> hook error:`, one `Permission for this command was ...`) and `user-rejected`
# (the user answered a permission prompt with no). Both are decided before the tool starts.
# `cancelled` is left out on purpose: it is what an interrupt writes, and an interrupt can land on a
# push that is already running.
NOT_RUN_DENIAL_KINDS = {"permission-rule", "user-rejected"}


def call_outcome(chain: list[dict], tool_use_id: str) -> bool | None:
    """Whether `tool_use_id` ran, read only from entries on this tool call's own chain.

    True: it ran (it has a result that is not a refusal). False: Claude Code refused it before it
    ran. None: it has no result on the chain yet -- still running, or issued in parallel with this
    one -- which the caller treats as having run. Only chain entries count, so a refusal appended to
    the transcript by anyone but Claude Code is never read (see point 1 of the module docstring).
    """
    if not any(holds_tool_use(e, tool_use_id) for e in chain):
        return None
    for e in chain:
        if e.get("type") != "user":
            continue
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        if not any(b.get("tool_use_id") == tool_use_id for b in results):
            continue
        # `toolDenialKind` belongs to the entry, so it can only describe a result that is alone.
        refused = (len(results) == 1 and results[0].get("is_error") is True
                   and e.get("toolDenialKind") in NOT_RUN_DENIAL_KINDS)
        return not refused
    return None


def spend(message_uuid: str, tool_use_id: str, branch: str, tip: str,
          ran=lambda _tool_use_id: True) -> str | None:
    """Record the spend; None when this push may go, else the reason it may not.

    A spend is recorded at PreToolUse, before the push runs, so that two pushes can never both
    hold one authorization. But a PreToolUse spend also lands for a push that another policy then
    denies (2026-10-20: the runtime-evidence guard denied `toolu_012cvxhZQpvkTq2UtRzsQwVy` after
    this signal had spent the user's authorization on it, and the user had to type it again). So
    the last record for a message names the call currently holding it, and a different call may
    take it over only when `ran(holder)` is False, meaning Claude Code recorded the holder as
    refused before it ran. A holder that ran, or whose outcome is not known yet, keeps it: one
    authorization still pays for exactly one push that ran.
    """
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        holders: list[str] = []
        refunded = False
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("message_uuid") == message_uuid:
                holders.append(rec.get("tool_use_id"))
                refunded = bool(rec.get("refunded"))
        record = {"message_uuid": message_uuid, "tool_use_id": tool_use_id,
                  "branch": branch, "tip": tip, "at": time.time()}
        if holders:
            holder = holders[-1]
            if holder == tool_use_id:
                return None  # the same call evaluated again, by a second hook
            if refunded:
                # The pre-push hook refused the holder's push for a precondition the agent can
                # clear (game running, dirty tree, ...) before any gate judged the content, and
                # recorded that with `scripts/er-pre-push-clear.py refund`. Nothing reached the
                # remote, so the reply is still the user's one push of this tip.
                record["replaces"] = holder
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(record) + "\n")
                return None
            if tool_use_id in holders:
                return "that authorization has moved on from this tool call to a later one"
            outcome = ran(holder) if isinstance(holder, str) else True
            if outcome is None:
                return ("that authorization is held by another push whose outcome is not in the "
                        "transcript yet")
            if outcome:
                return "that authorization was already spent on another push"
            record["replaces"] = holder
        fh.seek(0, os.SEEK_END)
        fh.write(json.dumps(record) + "\n")
        return None


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
        message_uuid, text, chain = prompt_and_chain(path, tool_use_id,
                                                     started + LOCATE_DEADLINE_SECONDS)
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
    refusal = spend(message_uuid, tool_use_id, branch, tip,
                    ran=lambda holder: call_outcome(chain, holder))
    if refusal is not None:
        raise Refused(refusal, branch, tip)
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
