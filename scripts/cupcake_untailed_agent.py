#!/usr/bin/env python3
"""Background subagents the main session started with no kitty view following them: what the Stop
guard `.cupcake/policies/claude/no_untailed_agent.rego` reads, through the signal
`.cupcake/signals/last_assistant_untailed_agent.sh`.

User, 2026-11-11, verbatim: "I don't understand how you could have a round running without me
being able to see the output". The main session had launched a background Agent ("Speed up attack
sets, round 2") and opened nothing the user could look at, although long background runs already
had to be tailed (`no_untailed_long_run`).

What counts, read from the main session's transcript:

* The window is everything after the last prompt that is not a Stop hook's feedback (`isMeta`),
  so a halt that was not acted on fires again when the continuation stops. Every other prompt the
  harness delivers -- a typed one, a task notification -- starts a new window, as it starts a new
  turn for `no_untailed_long_run`.
* A start is an Agent (or Task) call in the window whose result is the harness's background
  acknowledgement naming an id (`Async agent launched ... agentId: <id>`), or a SendMessage in the
  window answered `"resumedAgentId": "<id>"`, which sets a finished agent working again. A foreground
  agent's result is its whole output, so it needs no view.
* It is followed when a later Bash call in the window runs `scripts/kitty-tail-agent.py` with that
  id in its command line (`--dry-run` and the selftests open nothing and do not count). A call that
  failed still counts when assistant text after it mentions the tab, split or kitty, so a refusal
  the agent reports is not punished twice.
* An agent that is no longer working when the turn stops -- its latest notification says it
  finished or was stopped, or a TaskStop named it (`cupcake_turn_scan.subagent_states`) -- needs no
  view. "Is waiting on its own background work" is still working.

    python3 scripts/cupcake_untailed_agent.py --replay [N] [DIR]   # verdicts over N newest transcripts
"""
from __future__ import annotations

import glob
import os
import re
import shlex
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cupcake_turn_scan as scan  # noqa: E402

# The tab command counts only where it is run (after python3/bash/sh, or as the command itself), as
# no_untailed_long_run counts its long runs, so `grep <id> scripts/kitty-tail-agent.py` opens nothing.
_RUN = r"(?:\b(?:python3?|bash|sh)\s+(?:-[A-Za-z]\S*\s+)*|(?:^|[;&|]\s*)(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*)(?:[^\s'\"(]*/)?"
TAIL_RE = re.compile(_RUN + r"kitty-tail-agent\.py\b")
#: Flags with which the tab command opens nothing.
NO_TAB_RE = re.compile(r"--(?:dry-run|selftest|live-selftest|help)\b|\s-h\b")
TAB_WORD_RE = re.compile(r"\b(?:split|tab|kitty)\b", re.IGNORECASE)
TAG = "UNTAILEDAGENT:"
#: What the halt asks for, with the ids appended.
TAIL_COMMAND = "python3 scripts/kitty-tail-agent.py"


@dataclass
class Start:
    agent_id: str
    description: str
    order: int  # position of the result that started it, in content blocks since the window began


def window_start(events: list[dict]) -> int:
    """Index of the first event after the last prompt that is not a Stop hook's feedback."""
    start = 0
    for i, ev in enumerate(events):
        if scan.is_real_user_prompt(ev) and not ev.get("isMeta"):
            start = i + 1
    return start


def untailed_agents(events: list[dict]) -> list[Start]:
    """Every agent started in the window that is still working and that no later
    kitty-tail-agent.py call names, latest start per agent."""
    window = events[window_start(events):]
    pending: dict[str, tuple[str, str]] = {}   # tool_use id -> (kind, description or command)
    starts: dict[str, Start] = {}
    tails: list[tuple[int, bool, str]] = []   # (order, succeeded, command)
    texts: list[tuple[int, str]] = []
    order = 0
    for ev in window:
        if ev.get("isSidechain"):
            continue
        msg = ev.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            order += 1
            kind = block.get("type")
            if kind == "text" and ev.get("type") == "assistant":
                texts.append((order, block.get("text") or ""))
            elif kind == "tool_use":
                name = block.get("name") or ""
                inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                if name in ("Agent", "Task"):
                    pending[block.get("id") or ""] = ("agent", str(inp.get("description") or ""))
                elif name == "SendMessage":
                    pending[block.get("id") or ""] = ("resume", str(inp.get("to") or ""))
                elif name == "Bash":
                    cmd = inp.get("command") if isinstance(inp.get("command"), str) else ""
                    if TAIL_RE.search(cmd) and not NO_TAB_RE.search(cmd):
                        pending[block.get("id") or ""] = ("tail", cmd)
            elif kind == "tool_result":
                uid = block.get("tool_use_id") or ""
                if uid not in pending:
                    continue
                what, value = pending.pop(uid)
                if what == "tail":
                    tails.append((order, not block.get("is_error"), value))
                    continue
                if block.get("is_error"):
                    continue
                text = scan._result_text(block)
                if what == "agent":
                    ids = scan.AGENT_ID_RE.findall(text)
                    if ids and scan.BACKGROUND_LAUNCH_RE.search(text):
                        starts[ids[0]] = Start(ids[0], value, order)
                else:
                    resumed = scan.RESUMED_AGENT_RE.search(text)
                    if resumed:
                        prev = starts.get(resumed.group(1))
                        starts[resumed.group(1)] = Start(resumed.group(1), prev.description if prev else "", order)
    if not starts:
        return []
    working = {a.agent_id: a for a in scan.subagent_states(events) if a.state in ("running", "waiting")}

    def followed(s: Start) -> bool:
        for at, ok, cmd in tails:
            if at <= s.order or s.agent_id not in cmd:
                continue
            if ok or any(TAB_WORD_RE.search(t) for o, t in texts if o > at):
                return True
        return False

    out = []
    for s in starts.values():
        if s.agent_id not in working or followed(s):
            continue
        if not s.description:
            s.description = working[s.agent_id].description
        out.append(s)
    return out


def signal_line(events: list[dict]) -> str:
    """`UNTAILEDAGENT:<id> ("<description>"); ... => python3 scripts/kitty-tail-agent.py <id> ...`,
    or "" when every agent started in the window is followed or done."""
    missing = untailed_agents(events)
    if not missing:
        return ""
    named = []
    for s in missing:
        desc = " ".join(s.description.split())
        if len(desc) > 60:
            desc = desc[:57] + "..."
        named.append('%s ("%s")' % (s.agent_id, desc) if desc else s.agent_id)
    command = "%s %s" % (TAIL_COMMAND, " ".join(shlex.quote(s.agent_id) for s in missing))
    return (TAG + "; ".join(named))[:1200] + " => " + command


def replay(limit: int, tdir: str) -> int:
    """Judge every stop of the `limit` newest main-session transcripts in `tdir`: each turn's end,
    the way the Stop hook would have seen it, and print every one this guard would halt."""
    files = sorted(glob.glob(os.path.join(tdir, "*.jsonl")), key=os.path.getmtime, reverse=True)[:limit]
    stops = halts = launches = 0
    for path in files:
        events = scan.load_events(path)
        ends = [i for i, ev in enumerate(events) if scan.is_real_user_prompt(ev)] + [len(events)]
        for end in ends:
            if end == 0:
                continue
            stops += 1
            prefix = events[:end]
            window = prefix[window_start(prefix):]
            if not any("agentId:" in scan._result_text(b) or "resumedAgentId" in scan._result_text(b)
                       for ev in window if isinstance((ev.get("message") or {}).get("content"), list)
                       for b in ev["message"]["content"] if isinstance(b, dict) and b.get("type") == "tool_result"):
                continue
            launches += 1
            line = signal_line(prefix)
            if line:
                halts += 1
                print(f"{os.path.basename(path)}:{end}: {line[:300]}")
    print(f"replay: {len(files)} transcripts, {stops} stops, {launches} with an Agent or SendMessage "
          f"result naming an agent in their window, {halts} would halt")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--replay"]:
        limit = int(argv[1]) if len(argv) > 1 else 40
        cwd_key = "-" + os.getcwd().strip("/").replace("/", "-").replace(".", "-")
        tdir = argv[2] if len(argv) > 2 else os.path.join(os.path.expanduser("~/.claude/projects"), cwd_key)
        return replay(limit, tdir)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
