#!/usr/bin/env python3
"""Reader for the `task_stop_waiting_agent` cupcake signal.

What happened, 2026-11-11, session 5ec7b779: subagent abce87a3bda959d98 ("Strip losing rules, merge
dnok, test") notified `Agent "Strip losing rules, merge dnok, test" is waiting on its own background
work`. Its background Bash task b7rzimvi4, a long selftest, was still running. The main session
called TaskStop on the agent, the harness answered "Successfully stopped task", and the selftest died
with it: its output file ends in "[killed]". The main session did it because the Stop guard
`no_unclosed_finished_agents` tells it to close finished subagents, and it read the waiting agent as
one of them.

This reader answers one question for a pending TaskStop in the main session: is the task id an agent
the main session launched whose latest notification says it is waiting on its own background work,
with no later finished or stopped notification and no resume since? The agent's state comes from
`cupcake_turn_scan.subagent_states`, the reader the Stop guard uses, so the guard that says "close
it" and the guard that says "not yet" cannot disagree about an agent. For such an agent it prints

    WAITINGAGENT|id=<agent id>|override=<0|1>|description=<its description>|prompt=<first 160 chars
    of the user's last typed message>

and nothing for anything else: a bash or monitor task id, an agent that finished, was stopped or is
running, an id nobody launched.

Before the states are read, the TaskStop being judged is taken out of the transcript, along with any
earlier stop naming the same id that never got a result or that a PreToolUse hook refused. The state
reader counts a stop as a close whatever its result says, which is right for the Stop guard (an agent
that already completed answers "is not running") and wrong here: a stop this guard refused would
otherwise count as the close, and the retry would go through.

`override=1` means the user's own last typed message names the agent (its id, or a prefix of it at
least 7 characters long) or asks to stop it (stop, kill, cancel, abort or terminate, in the same
sentence as "agent"/"subagent" or the agent's description, and not negated). The message is read the
way `no_product_work_before_tool_fix` reads it, through `cupcake_tool_miss_report.prompt_for_call`
and the push guard's `prompt_text`: only a message the user typed counts, never assistant text, a
system reminder or a task notification. When no typed message can be found (a compaction summary
ends the walk), there is no override.

Fails open (prints nothing) on any other error, a subagent's call, or a transcript it cannot read.

`--selftest` runs the override classifier over its own corpus.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# A tool result that a PreToolUse hook refused: "PreToolUse:TaskStop hook error: ..." (the shape of
# every hook refusal in session 5ec7b779).
HOOK_REFUSAL_RE = re.compile(r"\A\s*(?:<tool_use_error>\s*)?PreToolUse:\w+ hook error\b")

STOP_VERB_RE = re.compile(r"\b(?:stop|kill|cancel|abort|terminate)\b", re.IGNORECASE)
# Words right before the verb that turn the sentence into something other than a request to stop:
# "don't stop the agent", "why did you kill the agent".
NEGATED_BEFORE_RE = re.compile(
    r"(?:\bdon'?t|\bdo\s+not|\bnever|\bnot|\bno\s+need\s+to|\bshouldn'?t|\bshould\s+not"
    r"|\bdid(?:n'?t)?\s+you|\bwhy\s+(?:did|would)\s+you)\s+$",
    re.IGNORECASE,
)
AGENT_WORD_RE = re.compile(r"\b(?:sub-?)?agents?\b", re.IGNORECASE)
ID_TOKEN_RE = re.compile(r"[A-Za-z0-9]{7,}")
SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")


def names_agent(prompt: str, agent_id: str) -> bool:
    return any(agent_id.startswith(tok) for tok in ID_TOKEN_RE.findall(prompt or ""))


def asks_to_stop(prompt: str, description: str) -> bool:
    desc = " ".join((description or "").lower().split())
    for sentence in SENTENCE_SPLIT_RE.split(prompt or ""):
        flat = " ".join(sentence.split())
        mentions = AGENT_WORD_RE.search(flat) or (desc and desc in flat.lower())
        if not mentions:
            continue
        for m in STOP_VERB_RE.finditer(flat):
            if not NEGATED_BEFORE_RE.search(flat[: m.start()]):
                return True
    return False


def overridden(prompt: str, agent_id: str, description: str) -> bool:
    return names_agent(prompt, agent_id) or asks_to_stop(prompt, description)


A = "abce87a3bda959d98"
D = "Strip losing rules, merge dnok, test"
SELFTEST = [
    ("stop abce87a3bda959d98", True),
    ("kill abce87a, the selftest is pointless now", True),
    ("abce87a3bda959d98 can go", True),
    ("stop the agent", True),
    ("Cancel the subagent, I'll rerun the selftest myself", True),
    ("terminate the strip losing rules, merge dnok, test one", True),
    ("Please abort all agents.", True),
    # Not a request to stop this agent.
    ("My largest concern is that every time I correct you, you fix the issue instead of fix it for "
     "future agents first", False),
    ("close finished subagents", False),
    ("don't stop the agent", False),
    ("Do not kill the subagent; its selftest is still running", False),
    ("why did you stop the agent?", False),
    ("stop the selftest monitor", False),
    ("the agent is slow. stop", False),
    ("abce87", False),
    ("authorize commit", False),
]


def selftest() -> int:
    bad = [(text, want) for text, want in SELFTEST if overridden(text, A, D) != want]
    for text, want in bad:
        print(f"FAIL expected {want}: {text}", file=sys.stderr)
    print("cupcake_task_stop_waiting_agent selftest:", "OK" if not bad else f"{len(bad)} failure(s)")
    return 1 if bad else 0


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            (sub.get("text") or "") if isinstance(sub, dict) else str(sub) for sub in content
        )
    return ""


def without_void_stops(events: list[dict], task_id: str, tool_use_id: str, stop_tools) -> list[dict]:
    """The events with every stop that has not closed anything taken out: the call being judged, and
    each earlier stop naming `task_id` that got no result or a PreToolUse hook refusal."""
    answered: dict[str, str] = {}
    for ev in events:
        content = (ev.get("message") or {}).get("content") if isinstance(ev.get("message"), dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    answered[str(block.get("tool_use_id") or "")] = (
                        _result_text(block) if block.get("is_error") else ""
                    )

    def void(block: object) -> bool:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            return False
        if block.get("name") not in stop_tools:
            return False
        bid = str(block.get("id") or "")
        if tool_use_id and bid == tool_use_id:
            return True
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        if str(inp.get("task_id") or inp.get("shell_id") or "") != task_id:
            return False
        return bid not in answered or bool(HOOK_REFUSAL_RE.search(answered[bid]))

    out: list[dict] = []
    for ev in events:
        msg = ev.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list) and any(void(b) for b in content):
            ev = dict(ev, message=dict(msg, content=[b for b in content if not void(b)]))
        out.append(ev)
    return out


def last_typed_prompt(path: str, tool_use_id: str) -> str | None:
    """The user's last typed message before this call, or None when none can be found."""
    import cupcake_push_authorization as shared
    import cupcake_tool_miss_report as miss

    try:
        if tool_use_id:
            # No wait for the call's own line: in the main session it lands only after the hooks
            # return, and the walk from the chain's head reaches the same prompt.
            _uuid, text = miss.prompt_for_call(shared, path, tool_use_id, time.monotonic())
        else:
            _uuid, text = miss.prompt_at_head(shared, path)
    except shared.Refused:
        return None
    return text


def judge(event: dict, raw_event: str) -> str:
    """The signal's line for one PreToolUse event, or "" for an allow."""
    import cupcake_turn_scan as scan

    if event.get("agent_id") or event.get("tool_name") != "TaskStop":
        return ""
    inp = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    task_id = str(inp.get("task_id") or inp.get("shell_id") or "").strip()
    if not task_id:
        return ""
    path = scan.hook_transcript(raw_event)
    if not path:
        return ""
    tool_use_id = str(event.get("tool_use_id") or "")
    events = without_void_stops(scan.load_events(path), task_id, tool_use_id, scan.STOP_TOOLS)
    agent = next((a for a in scan.subagent_states(events) if a.agent_id == task_id), None)
    if agent is None or agent.state != "waiting":
        return ""
    prompt = last_typed_prompt(path, tool_use_id) or ""
    override = overridden(prompt, agent.agent_id, agent.description)

    def clean(text: str, limit: int) -> str:
        return " ".join(text.split())[:limit].replace("|", "/")

    return (f"WAITINGAGENT|id={agent.agent_id}|override={int(override)}"
            f"|description={clean(agent.description, 120)}|prompt={clean(prompt, 160)}")


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    raw = sys.stdin.read()
    try:
        event = json.loads(raw or "{}")
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    try:
        line = judge(event, raw)
    except Exception:  # noqa: BLE001 -- fail open, see the module docstring
        return 0
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
