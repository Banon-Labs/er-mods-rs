#!/usr/bin/env python3
"""Live, ANSI-colored tail of a Claude Code dynamic-workflow run.

The /workflows TUI shows only a condensed tree. This renders the actual per-agent
activity of a workflow run -- text, tool calls, tool results, and the final
structured results -- interleaved and colorized, following live as the run writes.

A workflow run dir lives at:
  ~/.claude/projects/<proj>/<session>/subagents/workflows/wf_<id>/
containing journal.jsonl (workflow-level started/result events) and one
agent-<id>.jsonl (standard CC transcript) per sub-agent.

Usage:
  watch-workflow.py [RUNID|latest|/abs/run/dir] [--follow/--no-follow] [--full]
    RUNID    a wf_... id (resolved under the current project's sessions)
    latest   newest wf_* run dir under the project (default)
    --follow tail live (default); --no-follow render current content and exit
    --full   include assistant reasoning text (default: tool calls + results + results only-ish)

  watch-workflow.py --agent <agent id|/abs/agent-<id>.jsonl> [--full] [--linger S] [--no-follow]
    One background subagent: its transcript
    ~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl, rendered the same way and
    followed until the main session's transcript (<project>/<session>.jsonl) carries the agent's
    "finished" or "was stopped" task notification, or a TaskStop naming it. The view then prints
    that end, waits --linger seconds (default 5) and exits, so a kitty window running it closes.
    "Is waiting on its own background work" is not an end. scripts/kitty-tail-agent.py opens it.
"""
import glob
import json
import os
import re
import select
import shutil
import sys
import textwrap
import time
import threading

# Default caps on how much of a command result / reasoning block to show. These are
# display-only: the sub-agent always read the full result from its transcript; this only
# shortens the terminal view. Override with --result-lines N (0 = uncapped).
DEFAULT_RESULT_LINES = 24
MAX_TEXT_LINES = 10

PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")
HERE = os.path.dirname(os.path.abspath(__file__))
#: Seconds an agent view stays open after the agent ends, so its last lines can be read.
AGENT_LINGER_S = 5
AGENT_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

# --- ANSI ---
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GREY = "\033[90m"
AGENT_PALETTE = [
    "\033[38;5;39m", "\033[38;5;208m", "\033[38;5;170m", "\033[38;5;76m",
    "\033[38;5;214m", "\033[38;5;45m", "\033[38;5;204m", "\033[38;5;149m",
]



def bounded_poll_wait(seconds: float) -> None:
    """Bounded loop pacing; loop predicates still own readiness/stop decisions."""
    threading.Event().wait(min(max(float(seconds), 0.0), 30.0))

def agent_color(agent_id):
    return AGENT_PALETTE[hash(agent_id) % len(AGENT_PALETTE)]


def term_width():
    return max(60, shutil.get_terminal_size((120, 40)).columns)


def clip(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def wrap_block(text, width, max_lines, pad, bar_color, body_color):
    """Wrap a multiline blob into readable lines behind a thin colored gutter bar.

    The body is drawn in `body_color` (empty string = terminal default fg, i.e. full
    contrast on any theme); only the `│` gutter is colored, so a multi-line block is
    visually grouped without dimming the text the user is trying to read. max_lines<=0
    = uncapped.
    """
    gutter = f"{pad}{bar_color}│{RESET} "
    avail = max(20, width - len(pad) - 2)
    raw = str(text).replace("\t", "  ").rstrip().splitlines() or [""]
    lines = []
    for ln in raw:
        ln = ln.rstrip()
        if not ln:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(ln, avail, replace_whitespace=False, drop_whitespace=False) or [""])
    hidden = (len(lines) - max_lines) if max_lines and max_lines > 0 else 0
    if hidden > 0:
        lines = lines[:max_lines]
    out = [gutter + (f"{body_color}{ln}{RESET}" if body_color else ln) for ln in lines]
    if hidden > 0:
        out.append(f"{gutter}{DIM}… (+{hidden} more lines — rerun with --result-lines 0 for full){RESET}")
    return out


def find_run_dir(spec):
    """Resolve a run spec to a run dir path."""
    if spec and os.path.isdir(spec):
        return spec
    proj = None
    cwd_key = "-" + os.getcwd().strip("/").replace("/", "-")
    cand = os.path.join(PROJECTS_ROOT, cwd_key)
    if os.path.isdir(cand):
        proj = cand
    roots = [proj] if proj else glob.glob(os.path.join(PROJECTS_ROOT, "*"))
    runs = []
    for r in roots:
        runs += glob.glob(os.path.join(r, "*", "subagents", "workflows", "wf_*"))
    runs = [d for d in runs if os.path.isdir(d)]
    if spec and spec != "latest":
        hit = [d for d in runs if os.path.basename(d) == spec or spec in os.path.basename(d)]
        if hit:
            return max(hit, key=os.path.getmtime)
        print(f"no run matching {spec!r}; falling back to latest", file=sys.stderr)
    if not runs:
        return None
    return max(runs, key=os.path.getmtime)


def task_hint(path):
    """First readable line of an agent's opening prompt, to label the stream."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                o = json.loads(line)
                if o.get("type") != "user":
                    continue
                c = (o.get("message") or {}).get("content")
                if isinstance(c, str):
                    txt = c
                elif isinstance(c, list):
                    txt = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                else:
                    continue
                txt = txt.strip()
                if txt:
                    for ln in txt.splitlines():
                        if ln.strip():
                            return clip(ln.strip(), 90)
    except Exception:
        pass
    return ""


def brief_tool(inp):
    if not isinstance(inp, dict):
        return clip(inp, 100)
    for k in ("command", "file_path", "pattern", "description", "prompt", "query", "url", "skill"):
        if k in inp and inp[k]:
            return f"{k}={clip(inp[k], 110)}"
    return clip(json.dumps(inp), 100)


def render_agent_line(o, tag, color, w, full, result_lines):
    t = o.get("type")
    m = o.get("message") or {}
    out = []
    pre = f"{color}{tag}{RESET} "
    indent = " " * (len(tag) + 1)
    if t == "assistant" and isinstance(m.get("content"), list):
        for b in m["content"]:
            bt = b.get("type")
            if bt == "text" and full:
                txt = b.get("text", "").strip()
                if txt:
                    out.append(pre + f"{DIM}•{RESET}")
                    out += wrap_block(txt, w, MAX_TEXT_LINES, indent, color, "")
            elif bt == "tool_use":
                out.append(pre + f"{YELLOW}⚙ {b.get('name','?')}{RESET} {DIM}{clip(brief_tool(b.get('input')), w-len(tag)-16)}{RESET}")
    elif t == "user" and isinstance(m.get("content"), list):
        for b in m["content"]:
            if b.get("type") == "tool_result":
                cont = b.get("content")
                if isinstance(cont, list):
                    cont = "\n".join(x.get("text", "") for x in cont if isinstance(x, dict))
                err = bool(b.get("is_error"))
                bar = RED if err else color
                body = RED if err else ""  # "" = terminal default fg (full contrast)
                out.append(pre + f"{bar}└─ result{RESET}")
                out += wrap_block(cont, w, result_lines, indent, bar, body)
    return out


# --- one background subagent ---------------------------------------------------------------------

def agent_transcript(spec, root=None):
    """One subagent's transcript: `spec` is its path, or the agent id the main session's Agent
    result names (`agentId: <id>`), found as `<root>/<project>/<session>/subagents/agent-<id>.jsonl`
    -- the current project's sessions first, then every project's. None when there is none (yet)."""
    if spec and os.path.isfile(spec):
        return os.path.abspath(spec)
    if not spec or not AGENT_ID_RE.fullmatch(spec):
        return None
    root = root or PROJECTS_ROOT
    name = f"agent-{spec}.jsonl"
    here = glob.escape(os.path.join(root, "-" + os.getcwd().strip("/").replace("/", "-")))
    for proj in (here, os.path.join(glob.escape(root), "*")):
        hits = [p for p in glob.glob(os.path.join(proj, "*", "subagents", name)) if os.path.isfile(p)]
        if hits:
            return max(hits, key=os.path.getmtime)
    return None


def parent_transcript(path):
    """The main session's transcript for an agent transcript: `<project>/<session>.jsonl`, beside
    `<project>/<session>/subagents/agent-<id>.jsonl`. The agent's launch, its resumes and the task
    notifications that say it ended are written there, never in its own transcript."""
    return os.path.dirname(os.path.dirname(os.path.abspath(path))) + ".jsonl"


def agent_description(path):
    """The description the main session gave the agent, from `agent-<id>.meta.json`, or ""."""
    try:
        with open(path[: -len(".jsonl")] + ".meta.json", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return ""
    return str(meta.get("description") or "") if isinstance(meta, dict) else ""


def _turn_scan():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import cupcake_turn_scan
    return cupcake_turn_scan


class AgentStatus:
    """Whether one agent is still working, read from the main session's transcript as it grows.

    The agent's own transcript has no end marker: it stops at its last message whether the agent
    finished or parked on background jobs of its own. The main session's transcript says which, in
    the task notification the harness delivers there (the three summaries
    `cupcake_turn_scan.subagent_states` documents, surveyed over 1,942 notifications on 2026-11-11):
      `Agent "<description>" finished`                               -> finished
      `Agent "<description>" is waiting on its own background work`  -> waiting, not an end
      `Agent "<description>" was stopped by Claude`                  -> stopped
    A TaskStop naming the agent stops it as well, and a SendMessage answered `resumedAgentId` starts
    it again. Only lines that carry the agent id are parsed, in file order, so the latest one wins.
    """

    def __init__(self, main_path, agent_id):
        self.path = main_path
        self.agent_id = agent_id
        self.needle = agent_id.encode()
        self.offset = 0
        self.partial = b""
        self.state = "running"
        self.summary = ""
        self.scan = _turn_scan()

    def refresh(self):
        """Read what was appended since the last call; True when the state changed."""
        try:
            with open(self.path, "rb") as f:
                if os.fstat(f.fileno()).st_size < self.offset:
                    self.offset, self.partial = 0, b""
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return False
        self.offset += len(data)
        *lines, self.partial = (self.partial + data).split(b"\n")
        before = self.state
        for raw in lines:
            if self.needle in raw:
                self._line(raw)
        return self.state != before

    def _line(self, raw):
        s = self.scan
        try:
            ev = json.loads(raw)
        except ValueError:
            return
        if not isinstance(ev, dict) or ev.get("isSidechain") or ev.get("type") in s.REPLAY_EVENT_TYPES:
            return
        if ev.get("type") != "assistant":
            for text in s._notification_carriers(ev):
                for m in s.TASK_NOTIFICATION_BLOCK_RE.finditer(text):
                    body = m.group(1)
                    task_id = s.TASK_ID_RE.search(body)
                    if not task_id or task_id.group(1) != self.agent_id:
                        continue
                    summary = s.TASK_SUMMARY_RE.search(body)
                    parsed = s.AGENT_SUMMARY_RE.match(summary.group(1)) if summary else None
                    if parsed is None:
                        continue
                    verb = parsed.group(2).lower()
                    if verb == "finished":
                        state = "finished"
                    elif verb.startswith("is waiting on its own background work"):
                        state = "waiting"
                    elif verb.startswith("was stopped"):
                        state = "stopped"
                    else:
                        continue
                    self.state, self.summary = state, " ".join(summary.group(1).split())
        msg = ev.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            return
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") in s.STOP_TOOLS:
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                if str(inp.get("task_id") or inp.get("shell_id") or "") == self.agent_id:
                    self.state, self.summary = "stopped", f"{b.get('name')} named it"
            elif b.get("type") == "tool_result" and not b.get("is_error"):
                resumed = s.RESUMED_AGENT_RE.search(s._result_text(b))
                if resumed and resumed.group(1) == self.agent_id:
                    self.state, self.summary = "running", "resumed by SendMessage"


class Writes:
    """Wakes on a write to any of `paths` (inotify through cupcake_push_authorization.FileEvents),
    with `seconds` as the longest wait; a bounded poll where inotify is unavailable."""

    def __init__(self, paths):
        self.watches = []
        try:
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            from cupcake_push_authorization import IN_MODIFY, FileEvents
            for p in paths:
                if os.path.exists(p):
                    self.watches.append(FileEvents(p, IN_MODIFY))
        except Exception:
            self.close()

    def wait(self, seconds):
        if not self.watches:
            bounded_poll_wait(min(seconds, 0.6))
            return
        ready, _, _ = select.select([w.fd for w in self.watches], [], [], seconds)
        for fd in ready:
            try:
                os.read(fd, 65536)
            except BlockingIOError:
                pass

    def close(self):
        for w in self.watches:
            w.close()
        self.watches = []


def watch_agent(spec, follow, full, result_lines, linger):
    path = agent_transcript(spec)
    if not path:
        print(f"no transcript for agent {spec!r}: looked for "
              f"{PROJECTS_ROOT}/*/*/subagents/agent-{spec}.jsonl", file=sys.stderr)
        return 1
    agent_id = os.path.basename(path)[len("agent-"):-len(".jsonl")]
    desc = agent_description(path)
    main_path = parent_transcript(path)
    w = term_width()
    tag = f"[{agent_id[:6]}]"
    color = agent_color(agent_id)
    sys.stdout.write(f"\033]2;agent {clip(desc or agent_id, 40)}\007")
    print(f"{BOLD}{CYAN}▶ watching agent {agent_id}{RESET} {color}{desc}{RESET}")
    print(f"{DIM}{path}{RESET}")
    hint = task_hint(path)
    if hint:
        print(f"{color}{BOLD}▂ {tag} {RESET}{color}{hint}{RESET}")
    status = AgentStatus(main_path, agent_id) if os.path.isfile(main_path) else None
    if status is None:
        print(f"{YELLOW}no main-session transcript at {main_path}, so this view cannot see the agent "
              f"end; close it with Ctrl-C{RESET}")
    cap_desc = "uncapped" if result_lines <= 0 else f"{result_lines} lines/result"
    print(f"{DIM}{'follows until the agent ends' if follow else 'snapshot'} · "
          f"{'full' if full else 'tools+results'} · {cap_desc}{RESET}\n")

    offset = 0
    partial = b""

    def render():
        nonlocal offset, partial
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError:
            return
        offset += len(data)
        *lines, partial = (partial + data).split(b"\n")
        for raw in lines:
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if isinstance(o, dict):
                for out in render_agent_line(o, tag, color, w, full, result_lines):
                    print(out)
        sys.stdout.flush()

    def ended():
        return status is not None and status.state in ("finished", "stopped")

    writes = Writes([path, main_path]) if follow else None
    try:
        render()
        if status is not None:
            status.refresh()
        noted = None
        while follow and not ended():
            if status is not None and status.state != noted:
                if status.state == "waiting":
                    print(f"{YELLOW}… {status.summary}; still following{RESET}")
                elif status.state == "running" and noted is not None:
                    print(f"{GREEN}▶ {status.summary}{RESET}")
                sys.stdout.flush()
                noted = status.state
            writes.wait(2.0)
            render()
            if status is not None:
                status.refresh()
        render()
        if ended():
            mark = GREEN if status.state == "finished" else RED
            print(f"\n{BOLD}{mark}═══ agent {agent_id} {status.state}: {status.summary} ═══{RESET}")
            if follow and linger > 0:
                print(f"{DIM}closing in {linger} s{RESET}")
                sys.stdout.flush()
                bounded_poll_wait(linger)
        elif status is not None:
            print(f"\n{DIM}agent {agent_id} is {status.state}{RESET}")
    except KeyboardInterrupt:
        print(f"\n{DIM}(detached; the agent keeps going){RESET}")
    finally:
        if writes is not None:
            writes.close()
    return 0


def take_option(argv, flag):
    """Remove `flag VALUE` from argv and return `VALUE`; None when the flag is absent."""
    if flag not in argv:
        return None
    i = argv.index(flag)
    value = argv[i + 1] if i + 1 < len(argv) else None
    del argv[i:i + 2]
    return value


def main():
    argv = sys.argv[1:]
    agent = take_option(argv, "--agent")
    linger = take_option(argv, "--linger")
    follow = "--no-follow" not in argv
    full = "--full" in argv
    result_lines = DEFAULT_RESULT_LINES
    if "--result-lines" in argv:
        try:
            result_lines = int(argv[argv.index("--result-lines") + 1])
        except (ValueError, IndexError):
            pass
    if agent is not None:
        try:
            linger_s = int(linger) if linger is not None else AGENT_LINGER_S
        except ValueError:
            linger_s = AGENT_LINGER_S
        return watch_agent(agent, follow, full, result_lines, linger_s)
    positional = [a for a in argv if not a.startswith("--")]
    # Drop the value that follows --result-lines from positionals.
    if "--result-lines" in argv:
        val = argv[argv.index("--result-lines") + 1] if argv.index("--result-lines") + 1 < len(argv) else None
        positional = [p for p in positional if p != val]
    spec = positional[0] if positional else "latest"
    run = find_run_dir(spec)
    if not run:
        print("no workflow run dir found", file=sys.stderr)
        return 1
    w = term_width()
    # Self-title the tab/window (OSC 2) so it names the run even on the fallback paths.
    sys.stdout.write(f"\033]2;wf {os.path.basename(run)}\007")
    print(f"{BOLD}{CYAN}▶ watching workflow {os.path.basename(run)}{RESET}")
    print(f"{DIM}{run}{RESET}")
    cap_desc = "uncapped" if result_lines <= 0 else f"{result_lines} lines/result"
    print(f"{DIM}{'follow (Ctrl-C to exit)' if follow else 'snapshot'} · {'full' if full else 'tools+results'} · {cap_desc}{RESET}\n")

    offsets = {}
    labeled = set()
    started, resulted = set(), set()
    tags = {}

    def process():
        files = sorted(glob.glob(os.path.join(run, "agent-*.jsonl"))) + [os.path.join(run, "journal.jsonl")]
        for fp in files:
            if not os.path.exists(fp):
                continue
            is_journal = fp.endswith("journal.jsonl")
            aid = None if is_journal else os.path.basename(fp)[len("agent-"):-len(".jsonl")]
            if aid and aid not in tags:
                tags[aid] = f"[{aid[:6]}]"
            if aid and aid not in labeled:
                hint = task_hint(fp)
                labeled.add(aid)
                print(f"{agent_color(aid)}{BOLD}▂ {tags[aid]} {RESET}{agent_color(aid)}{hint}{RESET}")
            size = os.path.getsize(fp)
            off = offsets.get(fp, 0)
            if off > size:
                off = 0
            with open(fp, encoding="utf-8", errors="replace") as f:
                f.seek(off)
                chunk = f.read()
                offsets[fp] = f.tell()
            lines = chunk.split("\n")
            if not chunk.endswith("\n") and lines:
                # keep the partial last line for next poll
                offsets[fp] -= len(lines[-1].encode("utf-8", "replace"))
                lines = lines[:-1]
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if is_journal:
                    jt = o.get("type")
                    ja = o.get("agentId", "")
                    jtag = tags.get(ja, f"[{ja[:6]}]")
                    if jt == "started":
                        started.add(ja)
                        print(f"{GREEN}▶ {jtag} started{RESET}")
                    elif jt == "result":
                        resulted.add(ja)
                        res = o.get("result")
                        summ = ""
                        if isinstance(res, dict):
                            summ = res.get("summary") or json.dumps(res)
                        elif isinstance(res, str):
                            summ = res
                        print(f"{GREEN}{BOLD}✔ {jtag} RESULT{RESET} {GREEN}{clip(summ, w-16)}{RESET}")
                else:
                    for out in render_agent_line(o, tags[aid], agent_color(aid), w, full, result_lines):
                        print(out)
        if started and resulted >= started and follow:
            # all started agents have results -> run complete
            return True
        return False

    try:
        done = process()
        while follow and not done:
            bounded_poll_wait(0.6)
            done = process()
        if done:
            print(f"\n{BOLD}{GREEN}═══ RUN COMPLETE ({len(resulted)} agents) ═══{RESET}")
    except KeyboardInterrupt:
        print(f"\n{DIM}(detached; run keeps going){RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
