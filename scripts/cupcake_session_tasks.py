#!/usr/bin/env python3
"""What this Claude Code session has running: every background shell it or one of its subagents
started that is still alive, who started it and why, and every subagent still working.

    python3 scripts/cupcake_session_tasks.py            # this session (CLAUDE_CODE_SESSION_ID)
    python3 scripts/cupcake_session_tasks.py --session <id> [--json | --brief]
    python3 scripts/cupcake_session_tasks.py --hook --brief < <hook event>   # from a cupcake signal
    python3 scripts/cupcake_session_tasks.py --selftest

User directive 2026-11-11, after the agent named a subagent's shells only by reading their command
lines and then could not account for what the user's view showed: "I refuse to tell you, and
encourge you to find a way to self discover what shells you kick off. I didn't run any shells".
The harness shows the user a count of shells and agents; the agent sees only the task ids its own
calls returned, and a subagent's shells never reach the main transcript at all.

Two hooks read this report: the UserPromptSubmit context `session_tasks_context.rego` (signal
`.cupcake/signals/session_tasks.sh`) puts it in front of every turn while anything runs, and the
Stop guard `idle_hold.rego` (signal `.cupcake/signals/last_assistant_idle_hold.sh`) halts a paused
closing message that names none of what it lists.

# How a shell is found

The harness starts each Bash call as a descendant of the Claude Code process with stdout and stderr
on `<tasks dir>/<task id>.output`; a foreground call has one too, so a call still in the foreground
is found the same way. So a live process under that process that holds such a file open for
writing, at any of its descriptors, is that task still running. Every descriptor of every process
under Claude Code is read (`/proc/<pid>/fd/*`), and one that names a task's output counts when the
`flags` in its `/proc/<pid>/fdinfo/<fd>` open it write-only or read-write.

Any descriptor, not fd 1, because a shell running a redirected group (`{ ...; } > log 2>&1`) moves
its stdout and stderr to saved descriptors while the redirect is in force and fd 1 names the log.
Measured 2026-11-11 on task by9azikd7, a selftest run that way: its zsh had fds 1 and 2 on the log
and fds 12 and 14 on the task's output, the python it ran held only the log, and reading fd 1 alone
reported nothing running. bash saves them at 10 and 11 (measured the same day). Open for writing,
because a process that only reads a task's output, a `tail -f` of it run from another call, is not
that task. A shell that replaces both outright (`exec > log 2>&1`) keeps no descriptor of the output
and is not found (measured the same day).

The processes that hold the file are the task's shell and the children that inherited the
descriptor, and the one whose parent is not among them is its root. Its owner is the
transcript whose tool result acknowledges the launch (`Command running in background with ID: <id>`,
`... moved to the background (ID: <id>)`): the session's own (`~/.claude/projects/*/<session>.jsonl`)
or a subagent's (`<session>/subagents/agent-<agent id>.jsonl`, described by its `.meta.json`). A
call still in the foreground has no result yet, so it is matched by its command against the shell's
argv, among the calls of the session and of its working agents that have no result.

# How the Claude Code process is found

`CLAUDE_PID` when it is an ancestor of this process, else the nearest ancestor whose `comm` or
argv[0] is `claude`. A hook gets `CLAUDE_PID` as a Bash call does: read 2026-11-11 out of
/opt/claude-code/bin/claude, the hook runner builds the command's environment as `{...inherited,
...P8e(sessionId), CLAUDE_PROJECT_DIR}`, and P8e is the function that gives a Bash call
`CLAUDE_CODE_SESSION_ID` and `CLAUDE_PID: String(process.pid)`. The ancestor walk is there for a
harness that stops doing so, and the ancestor check for a `CLAUDE_PID` inherited from somewhere
else (a test run from a Claude Code shell pins its own pid, so the session it runs under is not read
as the fixture's).

# How an agent is found

`cupcake_turn_scan.subagent_states` over the session transcript, the reader the agent guards share:
an agent is working while its state is "running" or "waiting". A working subagent's own launches are
read the same way from its transcript.

# Cost

A hook pays for this on every prompt, and one session measured 2026-11-11 had 52 MB of transcript
and 199 MB of subagent transcripts: parsing all of it took 6.0 s, past the 5 s cupcake gives a
signal. So nothing is parsed whole. The agent states are read from the lines that can change one
(`AGENT_LINE_RE`), a launch is found by searching the raw bytes for its id, and a call still in the
foreground is looked for only in the last `PENDING_TAIL_BYTES` of a transcript. Descriptors are read
only for the processes under Claude Code, and fdinfo only for a descriptor naming a task's output:
measured 2026-11-11, 9 processes and 101 descriptors under it took 1 ms, the `stat` of all 520 on the
machine 13 ms.
"""
from __future__ import annotations

import argparse
import glob
import json
import mmap
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cupcake_turn_scan as scan  # noqa: E402

#: A task's output file: `<tmp>/claude-<uid>/<project>/<session-ish>/tasks/<task id>.output`.
OUTPUT_RE = re.compile(r"/tasks/([A-Za-z0-9]+)\.output$")
#: Every transcript line `subagent_states` can act on carries one of these: an Agent, SendMessage or
#: stop call, a launch result ("agentId:"), a resume ("resumedAgentId", "Resuming agent"), or a
#: notification. The rest is skipped without being parsed.
AGENT_LINE_RE = re.compile(
    rb'"(?:Agent|Task|SendMessage|TaskStop|KillShell|KillBash)"|task-notification|agentId|AgentId'
    rb"|Resuming agent")
#: The harness's request copies name their type near the start of the line; none is a conversation
#: event (`cupcake_turn_scan.REPLAY_EVENT_TYPES`).
REQUEST_COPY_RE = re.compile(rb'"type":\s*"api-request')
#: How a launch acknowledgement names its task, searched for in the raw transcript bytes.
LAUNCH_NEEDLE = b"ID: "
TASK_ID_AT_RE = re.compile(rb"[A-Za-z0-9_-]+")
#: How far back from a transcript's end a call still in the foreground is looked for. Such a call is
#: the transcript's last tool call or one of its parallel siblings, so only what was written after it
#: lies between it and the end.
PENDING_TAIL_BYTES = 2 << 20


@dataclass
class Shell:
    task_id: str
    pid: int
    age_s: float
    cpu_s: float
    pids: list[int] = field(default_factory=list)
    owner: str = "unknown"          # "main", or an agent id
    description: str = ""
    command: str = ""


def _stat(proc: str, pid: int) -> tuple[int, float, float] | None:
    """(parent pid, start in seconds since boot, CPU seconds) of `pid`, or None when it is gone."""
    try:
        rest = Path(f"{proc}/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    tck = os.sysconf("SC_CLK_TCK")
    return int(rest[1]), int(rest[19]) / tck, (int(rest[11]) + int(rest[12])) / tck


def _writes(proc: str, pid: int, fd: str) -> bool:
    """Whether `pid` holds descriptor `fd` open write-only or read-write: the access mode in the
    octal `flags` of its `/proc/<pid>/fdinfo/<fd>`. False when that cannot be read."""
    try:
        with open(f"{proc}/{pid}/fdinfo/{fd}") as fh:
            for row in fh:
                if row.startswith("flags:"):
                    return (int(row.split()[1], 8) & os.O_ACCMODE) in (os.O_WRONLY, os.O_RDWR)
    except (OSError, ValueError, IndexError):
        pass
    return False


def _task_writes(proc: str, pid: int) -> set[str]:
    """The task ids whose output file `pid` holds open for writing, at any descriptor."""
    out: set[str] = set()
    try:
        fds = os.listdir(f"{proc}/{pid}/fd")
    except OSError:
        return out
    for fd in fds:
        try:
            target = os.readlink(f"{proc}/{pid}/fd/{fd}")
        except OSError:
            continue
        m = OUTPUT_RE.search(target)
        if m and _writes(proc, pid, fd):
            out.add(m.group(1))
    return out


def live_shells(claude_pid: int, proc: str = "/proc") -> dict[str, Shell]:
    """{task id: Shell} of every process under `claude_pid` writing a task output file."""
    parent, start, cpu = {}, {}, {}
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        pid = int(name)
        st = _stat(proc, pid)
        if st is None:
            continue
        parent[pid], start[pid], cpu[pid] = st
    children: dict[int, list[int]] = {}
    for pid, pp in parent.items():
        children.setdefault(pp, []).append(pid)
    below, seen = [], set()
    todo = list(children.get(claude_pid, [])) if claude_pid > 1 else []
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.add(p)
            below.append(p)
            todo += children.get(p, [])
    up = float(Path(f"{proc}/uptime").read_text().split()[0])
    groups: dict[str, list[int]] = {}
    for pid in sorted(below):  # pid order, as /proc lists them: shells of one age keep launch order
        for tid in _task_writes(proc, pid):
            groups.setdefault(tid, []).append(pid)
    out = {}
    for tid, pids in groups.items():
        root = min((p for p in pids if parent.get(p) not in pids), key=lambda p: start[p])
        tree, todo = [], [root]
        while todo:
            p = todo.pop()
            tree.append(p)
            todo += children.get(p, [])
        out[tid] = Shell(tid, root, up - start[root], sum(cpu.get(p, 0.0) for p in tree), sorted(pids))
    return out


def _argv(proc: str, pid: int) -> str:
    try:
        return Path(f"{proc}/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def find_claude_pid(proc: str = "/proc", start: int | None = None, env=None) -> int:
    """The Claude Code process this one runs under, or 0 (see the module docstring)."""
    env = os.environ if env is None else env
    chain, pid, seen = [], os.getpid() if start is None else start, set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        st = _stat(proc, pid)
        if st is None:
            break
        pid = st[0]
        if pid > 1:
            chain.append(pid)
    want = str(env.get("CLAUDE_PID") or "")
    if want.isdigit() and int(want) in chain:
        return int(want)
    for pid in chain:
        try:
            comm = Path(f"{proc}/{pid}/comm").read_text().strip()
        except OSError:
            comm = ""
        argv0 = _argv(proc, pid).split(" ", 1)[0]
        if comm == "claude" or os.path.basename(argv0) == "claude":
            return pid
    return 0


def _norm(text: str) -> str:
    """Statement breaks as one form: the PreToolUse hook writes the transcript's `a; b` as `a\\nb`
    (measured 2026-11-11, a subagent's wait loop)."""
    return re.sub(r"\s*(?:;|\n)\s*", "\n", text)


def _tail(command: str) -> str:
    """The end of `command` as the harness's shell carries it: inside `eval '...'`, each `'` written
    `'"'"'`. The end, because a PreToolUse hook may rewrite the head (`rtk ls`)."""
    return _norm(command.strip().replace("'", "'\"'\"'"))[-160:]


def read_events(path: str, line_re: re.Pattern | None = None, tail: int = 0) -> list[dict]:
    """A transcript's events, parsing only the lines `line_re` finds (all when None), and only the
    last `tail` bytes when it is set (the partial first line dropped). [] when it cannot be read."""
    out: list[dict] = []
    try:
        fh = open(path, "rb")
    except OSError:
        return out
    with fh:
        if tail:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - tail))
            if size > tail:
                fh.readline()
        for raw in fh:
            if line_re is not None and not line_re.search(raw):
                continue
            if REQUEST_COPY_RE.search(raw, 0, 300):
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("type") not in scan.REPLAY_EVENT_TYPES:
                out.append(ev)
    return out


def _bash_calls(events: list[dict]) -> list[tuple[str, str]]:
    """(description, command) of the Bash calls in `events` that have no result in them."""
    calls = {}
    for ev in events:
        msg = ev.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        for b in content if isinstance(content, list) else ():
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                calls[b.get("id")] = (str(inp.get("description") or ""), str(inp.get("command") or ""))
            elif isinstance(b, dict) and b.get("type") == "tool_result":
                calls.pop(b.get("tool_use_id"), None)
    return list(calls.values())


def pending_calls(path: str) -> list[tuple[str, str]]:
    """(description, command) of a transcript's Bash calls that have no result yet: still running in
    the foreground, so their task id is not written anywhere but the process table."""
    return _bash_calls(read_events(path, tail=PENDING_TAIL_BYTES))


def _line_at(buf, i: int) -> tuple[int, bytes]:
    s = buf.rfind(b"\n", 0, i) + 1
    e = buf.find(b"\n", i)
    return s, bytes(buf[s:len(buf) if e < 0 else e])


def _launch_at(buf, i: int, tid: str) -> tuple[str, str] | None:
    """(description, command) of the Bash call whose launch acknowledgement holds offset `i` and
    names `tid`, or None when that offset is not one (a grep printing the words, say)."""
    s, line = _line_at(buf, i)
    try:
        ev = json.loads(line)
    except ValueError:
        return None
    msg = ev.get("message") if isinstance(ev, dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    for b in content if isinstance(content, list) else ():
        if not (isinstance(b, dict) and b.get("type") == "tool_result"):
            continue
        m = scan.RUNNING_TASK_LAUNCH_RE.search(scan._result_text(b))
        if not m or (m.group(1) or m.group(2)) != tid:
            continue
        use = str(b.get("tool_use_id") or "").encode()
        for needle in (b'"id":"' + use + b'"', b'"id": "' + use + b'"'):
            k = buf.rfind(needle, 0, s) if use else -1
            if k < 0:
                continue
            try:
                call = json.loads(_line_at(buf, k)[1])
            except ValueError:
                break
            cmsg = call.get("message") if isinstance(call, dict) else None
            for c in (cmsg.get("content") if isinstance(cmsg, dict) else None) or ():
                if isinstance(c, dict) and c.get("id") == use.decode() and isinstance(c.get("input"), dict):
                    inp = c["input"]
                    return str(inp.get("description") or ""), str(inp.get("command") or "")
            break
        return "", ""
    return None


def launches(paths: list[str], task_ids) -> dict[str, tuple[str, str, str]]:
    """{task id: (transcript, description, command)} for each of `task_ids` whose launch one of
    `paths` acknowledges, the first transcript that does winning. The raw bytes are searched for
    `ID: <task id>`, and only the lines that hold one are parsed."""
    want, found = set(task_ids), {}
    for path in paths:
        if not want - found.keys():
            break
        try:
            fh = open(path, "rb")
        except OSError:
            continue
        with fh:
            if os.fstat(fh.fileno()).st_size == 0:
                continue
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as buf:
                i = buf.find(LAUNCH_NEEDLE)
                while i >= 0 and want - found.keys():
                    j = i + len(LAUNCH_NEEDLE)
                    m = TASK_ID_AT_RE.match(buf, j)
                    tid = m.group(0).decode() if m else ""
                    if tid in want and tid not in found:
                        hit = _launch_at(buf, i, tid)
                        if hit is not None:
                            found[tid] = (path, *hit)
                    i = buf.find(LAUNCH_NEEDLE, j)
    return found


def working_agents(path: str, sidechain: bool = False, events: list[dict] | None = None) -> list[scan.Subagent]:
    """The agents a transcript launched that are running or waiting on their own work. A subagent's
    transcript marks every event a sidechain, which `subagent_states` skips, so it is unmarked.
    `events` stands in for reading `path` when the caller has them already."""
    if events is None:
        events = read_events(path, AGENT_LINE_RE)
    if sidechain:
        events = [{**e, "isSidechain": False} for e in events]
    return [a for a in scan.subagent_states(events) if a.state in ("running", "waiting")]


def _meta_description(sub_dir: str, aid: str) -> str:
    try:
        return str(json.loads(Path(sub_dir, f"agent-{aid}.meta.json").read_text()).get("description") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def session_report(session: str, claude_pid: int, projects: str | None = None, proc: str = "/proc",
                   transcript: str | None = None, main_events: list[dict] | None = None) -> dict:
    """The session's live shells with their owners, and its working agents (each with its own).
    `transcript` names the session's transcript instead of finding it under `projects`;
    `main_events`, when the caller has parsed it already, stands in for reading its agent lines."""
    main = transcript
    if not main:
        hits = glob.glob(os.path.join(projects or os.path.expanduser("~/.claude/projects"), "*",
                                      f"{session}.jsonl"))
        if not hits:
            raise SystemExit(f"no transcript for session {session} under {projects}")
        main = hits[0]
    sub_dir = os.path.join(main[:-len(".jsonl")], "subagents")

    def sub_file(aid: str) -> str:
        return os.path.join(sub_dir, f"agent-{aid}.jsonl")
    agents = [(a, "main") for a in working_agents(main, events=main_events)]
    todo = [a.agent_id for a, _ in agents]
    seen = set(todo)
    while todo:
        aid = todo.pop()
        for a in working_agents(sub_file(aid), sidechain=True) if os.path.isfile(sub_file(aid)) else ():
            if a.agent_id not in seen:
                seen.add(a.agent_id)
                agents.append((a, aid))
                todo.append(a.agent_id)
    working = [a.agent_id for a, _ in agents]

    shells = live_shells(claude_pid, proc)
    if shells:
        first = [sub_file(a) for a in working]
        rest = sorted(set(glob.glob(os.path.join(sub_dir, "agent-*.jsonl"))) - set(first),
                      key=lambda f: -os.path.getmtime(f))
        for tid, (path, desc, cmd) in launches([main] + first + rest, shells).items():
            aid = "main" if path == main else os.path.basename(path)[len("agent-"):-len(".jsonl")]
            shells[tid].owner, shells[tid].description, shells[tid].command = aid, desc, cmd
    unowned = [s for s in shells.values() if s.owner == "unknown"]
    if unowned:
        me = {os.getpid(), os.getppid()}
        pending = [("main", *v) for v in pending_calls(main)]
        for aid in working:
            pending += [(aid, *v) for v in pending_calls(sub_file(aid))]
        for s in unowned:
            argv = _norm(_argv(proc, s.pid))
            for who, desc, cmd in pending:
                if cmd.strip() and _tail(cmd) in argv:
                    s.owner, s.description, s.command = who, desc + " (in the foreground)", cmd
                    break
            else:
                if me & set(s.pids):
                    s.description = "this report's own call"
    owners = {s.owner for s in shells.values()} | set(working)
    descriptions = {aid: _meta_description(sub_dir, aid) for aid in owners if aid not in ("main", "unknown")}
    return {"session": session, "claude_pid": claude_pid, "shells": sorted(shells.values(), key=lambda s: -s.age_s),
            "agents": agents, "descriptions": descriptions}


def hook_report(event: dict | None, proc: str = "/proc", main_events: list[dict] | None = None,
                env=None) -> dict | None:
    """The report for the session a hook event names (its `transcript_path`, else its `session_id`),
    under the Claude Code process this one runs under. None when either cannot be found: a hook
    that cannot tell whose session it is reports nothing."""
    if not isinstance(event, dict):
        return None
    transcript = scan.hook_transcript(json.dumps(event))
    claude_pid = find_claude_pid(proc, env=env)
    if not transcript or not claude_pid:
        return None
    session = str(event.get("session_id") or os.path.basename(transcript)[:-len(".jsonl")])
    return session_report(session, claude_pid, proc=proc, transcript=transcript, main_events=main_events)


def live(rep: dict | None) -> bool:
    return bool(rep and (rep["shells"] or rep["agents"]))


def span(s: float) -> str:
    s = round(s)
    return f"{s}s" if s < 60 else f"{s // 60}m{s % 60:02d}s" if s < 3600 else f"{s // 3600}h{s % 3600 // 60:02d}m"


def _who(rep: dict, owner: str) -> str:
    if owner == "main":
        return "main session"
    if owner == "unknown":
        return "owner unknown"
    desc = rep["descriptions"].get(owner, "")
    return f"agent {owner} ({desc})" if desc else f"agent {owner}"


def _what(s: Shell, width: int = 80) -> str:
    return s.description or (s.command.strip().splitlines() or [""])[0][:width]


def render(rep: dict) -> str:
    lines = [f"session {rep['session']} (Claude Code pid {rep['claude_pid']})",
             f"background shells running: {len(rep['shells'])}"]
    for s in rep["shells"]:
        lines.append(f"  {s.task_id}  pid {s.pid}  up {span(s.age_s)}  cpu {s.cpu_s:,.0f} s  "
                     f"{len(s.pids)} proc  {_who(rep, s.owner)}: {_what(s)}")
    lines.append(f"agents working: {len(rep['agents'])}")
    for a, parent in rep["agents"]:
        mine = [s.task_id for s in rep["shells"] if s.owner == a.agent_id]
        by = "main session" if parent == "main" else f"agent {parent}"
        lines.append(f"  {a.agent_id}  {a.state}  {a.description}  (launched by {by}; shells: "
                     f"{', '.join(mine) or 'none'})")
    return "\n".join(lines)


def brief(rep: dict | None, limit: int = 8) -> str:
    """The report in one paragraph, for a hook to put in front of the agent; "" when nothing runs."""
    if not live(rep):
        return ""
    parts = []
    shells = rep["shells"]
    if shells:
        listed = [f"{s.task_id} ({_who(rep, s.owner)}: {_what(s, 60)}; up {span(s.age_s)})" for s in shells[:limit]]
        more = f"; and {len(shells) - limit} more" if len(shells) > limit else ""
        parts.append(f"Shells running: {'; '.join(listed)}{more}.")
    agents = rep["agents"]
    if agents:
        listed = [f"{a.agent_id} '{a.description}' ({a.state}"
                  f"{'' if parent == 'main' else ', launched by agent ' + parent})" for a, parent in agents[:limit]]
        more = f"; and {len(agents) - limit} more" if len(agents) > limit else ""
        parts.append(f"Agents working: {'; '.join(listed)}{more}.")
    return " ".join(parts)


def as_json(rep: dict) -> dict:
    return {
        "session": rep["session"], "claude_pid": rep["claude_pid"],
        "shells": [{"task_id": s.task_id, "pid": s.pid, "pids": s.pids, "age_s": round(s.age_s, 1),
                    "cpu_s": round(s.cpu_s, 1), "owner": s.owner,
                    "owner_description": rep["descriptions"].get(s.owner, ""),
                    "description": s.description, "command": s.command} for s in rep["shells"]],
        "agents": [{"agent_id": a.agent_id, "state": a.state, "description": a.description,
                    "launched_by": parent} for a, parent in rep["agents"]],
    }


def _word(token: str, text: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9_-])" + re.escape(token) + r"(?![A-Za-z0-9_-])", text))


def named_blocker(text: str, rep: dict | None, extra_ids=()) -> str:
    """The first item of the report that `text` names, or "": a live shell's task id, one of
    `extra_ids` (a caller's own task ids, such as a Monitor the transcript shows running), or a
    working agent by its id, a prefix of it at least 7 characters long (the harness's own short
    form, "Resuming agent a4d14f2"), or its description, spaces and case aside."""
    if not rep:
        return ""
    for tid in [s.task_id for s in rep["shells"]] + [t for t in extra_ids if t]:
        if _word(tid, text):
            return tid
    flat = " ".join(text.split()).casefold()
    for a, _parent in rep["agents"]:
        if _word(a.agent_id, text):
            return a.agent_id
        for m in re.finditer(r"(?<![A-Za-z0-9_-])[A-Za-z0-9]{7,}(?![A-Za-z0-9_-])", text):
            if a.agent_id.startswith(m.group(0)):
                return a.agent_id
        desc = " ".join(a.description.split()).casefold()
        if desc and desc in flat:
            return a.agent_id
    return ""


def selftest() -> int:
    import subprocess
    import tempfile
    fails = []
    total = 0

    def check(label, got, want):
        nonlocal total
        total += 1
        print(f"{'ok' if got == want else 'FAIL'}  {label}: {got!r}")
        if got != want:
            fails.append(label)

    def line(**ev):
        return json.dumps(ev) + "\n"

    def call(cid, tid, desc, cmd="sleep 30"):
        return (line(type="assistant", uuid=cid + "u", message={"content": [
                    {"type": "tool_use", "id": cid, "name": "Bash",
                     "input": {"command": cmd, "description": desc, "run_in_background": True}}]}),
                line(type="user", uuid=cid + "r", message={"content": [
                    {"type": "tool_result", "tool_use_id": cid,
                     "content": f"Command running in background with ID: {tid}. Output is being written to: x"}]}))
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "projects", "-r")
        sess = "s1"
        os.makedirs(os.path.join(proj, sess, "subagents"))
        tasks = os.path.join(tmp, "claude-1", "-r", "s0", "tasks")
        os.makedirs(tasks)
        m1, m2 = call("t1", "bmain1", "main run")
        f1, f2 = call("t2", "bdone2", "finished run")
        launch = line(type="assistant", uuid="la", message={"content": [
            {"type": "tool_use", "id": "ag", "name": "Agent", "input": {"description": "Speed it up"}}]})
        launched = line(type="user", uuid="lr", message={"content": [
            {"type": "tool_result", "tool_use_id": "ag",
             "content": "Async agent launched successfully. agentId: aSUB1 (internal ID)"}]})
        # A grep that printed a launch acknowledgement is not one: bother9 stays unclaimed.
        g1 = line(type="assistant", uuid="gu", message={"content": [
            {"type": "tool_use", "id": "tg", "name": "Bash", "input": {"command": "grep -r ID: tests"}}]})
        g2 = line(type="user", uuid="gr", message={"content": [
            {"type": "tool_result", "tool_use_id": "tg",
             "content": "tests/x.py: 'Command running in background with ID: bother9.'"}]})
        Path(proj, f"{sess}.jsonl").write_text(m1 + m2 + f1 + f2 + launch + launched + g1 + g2)
        s1, s2 = call("t3", "bsub3", "", "python3 bench.py --jobs 2")
        p1, _ = call("t4", "bfg4", "wait for the 'bench' log", "true; sleep 30 & wait # the 'bench' log")
        Path(proj, sess, "subagents", "agent-aSUB1.jsonl").write_text(
            s1.replace('"uuid"', '"isSidechain": true, "uuid"') + s2 + p1)
        Path(proj, sess, "subagents", "agent-aSUB1.meta.json").write_text(json.dumps({"description": "Speed it up"}))
        Path(proj, "quiet.jsonl").write_text(f1 + f2)
        procs = []
        for tid, script in (("bmain1", "sleep 30 & wait"), ("bsub3", "sleep 30 & wait"),
                            ("bother9", "sleep 30 & wait"),
                            ("bfg4", "eval 'true\nsleep 30 & wait # the '\"'\"'bench'\"'\"' log' < /dev/null")):
            out = open(os.path.join(tasks, f"{tid}.output"), "w")
            procs.append(subprocess.Popen(["bash", "-c", script], stdout=out, stderr=out, start_new_session=True))
            out.close()
        hook_env = {**os.environ, "CLAUDE_PID": str(os.getpid())}

        def hook(transcript, *flags):
            event = json.dumps({"session_id": os.path.basename(transcript)[:-6], "transcript_path": transcript,
                                "hook_event_name": "UserPromptSubmit"})
            return subprocess.run([sys.executable, os.path.abspath(__file__), "--hook", *flags], input=event,
                                  capture_output=True, text=True, env=hook_env, timeout=20).stdout
        try:
            # Each shell forks a `sleep`; the checks below need the child (`len(pids) >= 2`). Each
            # look reads /proc, which paces the loop.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not all(
                    Path(f"/proc/{p.pid}/task/{p.pid}/children").read_text().split() for p in procs):
                pass
            rep = session_report(sess, os.getpid(), os.path.join(tmp, "projects"))
            got = {s.task_id: (s.owner, s.description or s.command, len(s.pids) >= 2) for s in rep["shells"]}
            check("live shells: the main one, the subagent's (named by its command), an unclaimed one; "
                  "a finished task is not listed; a shell and its child share a task", got,
                  {"bmain1": ("main", "main run", True), "bsub3": ("aSUB1", "python3 bench.py --jobs 2", True),
                   "bother9": ("unknown", "", True),
                   "bfg4": ("aSUB1", "wait for the 'bench' log (in the foreground)", True)})
            check("agents working: the launched one, by the main session",
                  [(a.agent_id, a.state, p) for a, p in rep["agents"]], [("aSUB1", "running", "main")])
            text = render(rep)
            check("render names the owner agent and its shells",
                  ("agent aSUB1 (Speed it up)" in text, "shells: bsub3" in text), (True, True))
            outside = live_shells(1 << 30)
            check("a process outside the given Claude Code pid is not this session's", outside, {})

            out = hook(os.path.join(proj, f"{sess}.jsonl"), "--brief")
            check("--hook --brief, a real hook event and real children: every shell id, its owner, the agent",
                  [k in out for k in ("bmain1 (main session: main run;", "bsub3 (agent aSUB1 (Speed it up)",
                                      "bother9 (owner unknown", "aSUB1 'Speed it up' (running)")],
                  [True] * 4)
            try:
                js = json.loads(hook(os.path.join(proj, f"{sess}.jsonl"), "--json"))
                got = (sorted(s["task_id"] for s in js["shells"]), [a["agent_id"] for a in js["agents"]],
                       next(s["owner_description"] for s in js["shells"] if s["task_id"] == "bsub3"))
            except (ValueError, StopIteration, KeyError) as exc:
                got = repr(exc)
            check("--hook --json carries the same report", got,
                  (["bfg4", "bmain1", "bother9", "bsub3"], ["aSUB1"], "Speed it up"))
            check("--hook with stdin that is not an event prints nothing",
                  subprocess.run([sys.executable, os.path.abspath(__file__), "--hook", "--brief"], input="nope",
                                 capture_output=True, text=True, env=hook_env, timeout=20).stdout, "")

            check("named_blocker: a shell id, an agent description, a 7-char agent prefix, nothing",
                  [named_blocker("Blocked on bsub3.", rep), named_blocker("Waiting for the speed it up agent.", rep),
                   named_blocker("Blocked on aSUB1.", rep), named_blocker("Blocked on the bench run.", rep),
                   named_blocker("Blocked on bmon7.", rep, extra_ids=["bmon7"]),
                   named_blocker("bsub3x is not bsub3's id", {**rep, "shells": rep["shells"][:0]})],
                  ["bsub3", "aSUB1", "aSUB1", "", "bmon7", ""])
            long_id = {**rep, "shells": [], "agents": [(scan.Subagent("a740eb3ea960d3d6b", "Speed up", "running", 0), "main")]}
            check("named_blocker: the harness's 7-char short id names the agent; 6 characters do not",
                  [named_blocker("Resuming agent a740eb3.", long_id), named_blocker("agent a740eb is up", long_id)],
                  ["a740eb3ea960d3d6b", ""])
        finally:
            for p in procs:
                os.killpg(p.pid, signal.SIGKILL)  # the shell and its sleep: its own session
                p.wait()
        check("--hook --brief prints nothing when nothing of the session runs",
              hook(os.path.join(proj, "quiet.jsonl"), "--brief"), "")

        fake = os.path.join(tmp, "proc")
        for pid, ppid, comm, argv in ((500, 400, "python3", "python3 x"), (400, 300, "bash", "bash"),
                                      (300, 200, "claude", "/opt/claude-code/bin/claude"),
                                      (200, 1, "fish", "fish"), (600, 1, "node", "claude --resume")):
            os.makedirs(os.path.join(fake, str(pid)))
            Path(fake, str(pid), "stat").write_text(f"{pid} ({comm}) S {ppid} " + "0 " * 40)
            Path(fake, str(pid), "comm").write_text(comm + "\n")
            Path(fake, str(pid), "cmdline").write_bytes(argv.replace(" ", "\0").encode() + b"\0")
        check("find_claude_pid: CLAUDE_PID when it is an ancestor; the nearest claude when it is not, or unset; "
              "0 with none above",
              [find_claude_pid(fake, 500, {"CLAUDE_PID": "400"}), find_claude_pid(fake, 500, {"CLAUDE_PID": "600"}),
               find_claude_pid(fake, 500, {}), find_claude_pid(fake, 200, {})],
              [400, 300, 300, 0])
        Path(fake, "300", "comm").write_text("MainThread\n")
        check("find_claude_pid: argv[0] named claude when comm is not", find_claude_pid(fake, 500, {}), 300)

        # A redirected group, as task by9azikd7 ran 2026-11-11: the shell's fds 1 and 2 are on the
        # log and its saved fds 12 and 14 on the task's output (O_WRONLY|O_LARGEFILE); the child it
        # ran holds only the log. Another call's shell reads that output (O_RDONLY) and is not that
        # task; one writing through an O_RDWR descriptor is; one outside Claude Code is not listed.
        tck = os.sysconf("SC_CLK_TCK")
        fdp = os.path.join(tmp, "fdproc")
        out_of = os.path.join(tmp, "claude-1", "-r", "s0", "tasks", "{}.output").format
        log = os.path.join(tmp, "scratch", "selftests.log")
        layout = (
            (300, 1, "claude", 0, 0, {}),
            (400, 300, "zsh", 50 * tck, 2 * tck, {1: (log, "0100001"), 2: (log, "0100001"),
                                                   12: (out_of("bgrp5"), "0100001"),
                                                   14: (out_of("bgrp5"), "0100001")}),
            (401, 400, "python3", 51 * tck, 30 * tck, {1: (log, "0100001"), 2: (log, "0100001")}),
            (410, 300, "zsh", 60 * tck, 0, {1: (out_of("btail6"), "0102001"), 2: (out_of("btail6"), "0102001"),
                                            3: (out_of("bgrp5"), "0100000"), 4: (out_of("bdone7"), "0100000")}),
            (420, 300, "python3", 70 * tck, 0, {5: (out_of("brdwr8"), "0100002")}),
            (430, 1, "bash", 80 * tck, 0, {1: (out_of("bstray9"), "0100001")}))
        for pid, ppid, comm, begin, utime, fds in layout:
            os.makedirs(os.path.join(fdp, str(pid), "fd"))
            os.makedirs(os.path.join(fdp, str(pid), "fdinfo"))
            fields = ["S", str(ppid)] + ["0"] * 38
            fields[11], fields[19] = str(utime), str(begin)
            Path(fdp, str(pid), "stat").write_text(f"{pid} ({comm}) " + " ".join(fields))
            for fd, (target, flags) in fds.items():
                os.symlink(target, os.path.join(fdp, str(pid), "fd", str(fd)))
                Path(fdp, str(pid), "fdinfo", str(fd)).write_text(f"pos:\t0\nflags:\t{flags}\nmnt_id:\t30\n")
        Path(fdp, "uptime").write_text("100.00 400.00\n")
        got = {t: (s.pid, s.pids, round(s.age_s), round(s.cpu_s)) for t, s in live_shells(300, fdp).items()}
        check("fd scan: a redirected group's saved fds name its task, its child's CPU counted; a reader is "
              "not the task it reads; O_RDWR writes; a writer outside Claude Code is not listed", got,
              {"bgrp5": (400, [400], 50, 32), "btail6": (410, [410], 40, 0), "brdwr8": (420, [420], 30, 0)})

        mixed = os.path.join(tmp, "mixed.jsonl")
        Path(mixed).write_text(launch + launched + m1 + m2 + line(type="api-request-blob", message={"content": [
            {"type": "tool_result", "tool_use_id": "ag", "content": "Async agent launched successfully. agentId: aCOPY"}]}))
        check("the agent-line filter reads the agents a whole parse does, request copies left out",
              [(a.agent_id, a.state) for a in scan.subagent_states(read_events(mixed, AGENT_LINE_RE))],
              [(a.agent_id, a.state) for a in scan.subagent_states(scan.load_events(mixed))])
        check("brief is empty with nothing running",
              brief({"session": "q", "claude_pid": 1, "shells": [], "agents": [], "descriptions": {}}), "")
    print(f"cupcake_session_tasks selftest {'ok' if not fails else 'FAILED'} ({total - len(fails)}/{total})")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    ap.add_argument("--claude-pid", type=int, default=0,
                    help="default: CLAUDE_PID when it is an ancestor, else the nearest `claude` ancestor")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--hook", action="store_true",
                    help="read a hook event on stdin and report its session; prints nothing it cannot measure")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--brief", action="store_true", help="one paragraph, empty when nothing runs")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.hook:
        try:
            rep = hook_report(json.loads(sys.stdin.read() or "null"))
        except Exception:  # a hook must never wedge a session: no report is "say nothing"
            return 0
        if rep is None or (a.brief and not live(rep)):
            return 0
    else:
        claude_pid = a.claude_pid or find_claude_pid()
        if not a.session or not claude_pid:
            ap.error("no session: run it from a Claude Code shell (CLAUDE_CODE_SESSION_ID) or pass --session "
                     "and --claude-pid")
        rep = session_report(a.session, claude_pid, a.projects)
    if a.json:
        print(json.dumps(as_json(rep)))
    elif a.brief:
        print(brief(rep))
    else:
        print(render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
