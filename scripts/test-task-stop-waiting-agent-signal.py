#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `task_stop_waiting_agent`.

The signal prints `WAITINGAGENT|id=<id>|override=<0|1>|description=...|prompt=...` for a TaskStop in
the main session whose task id is a subagent still waiting on its own background work, and nothing
otherwise; `no_stop_waiting_agent` denies on `override=0`. The incident (2026-11-11, session 5ec7b779):
a TaskStop on agent abce87a3bda959d98, whose latest notification said it "is waiting on its own
background work", killed its running selftest b7rzimvi4.

Each case writes a crafted transcript and hands the signal a PreToolUse event for the TaskStop, the way
cupcake does. The launch, notification and stop texts are the harness's own, copied from session
5ec7b779. The last two cases replay that session's own transcript up to the TaskStop call, without
and with the call's own line on disk, and are skipped when the transcript is not on this machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "task_stop_waiting_agent.sh"

A1 = "abce87a3bda959d98"
D1 = "Strip losing rules, merge dnok, test"
A2 = "ac34f26adeea54127"
D2 = "Fix wall-of-text count of reply line"
STOP_ID = "toolu_stop_under_judgement"

REPLAY = Path.home() / ".claude/projects/-home-banon-projects-er-mods-rs/5ec7b779-1950-45a6-84dd-ef5b2c3e9082.jsonl"
REPLAY_STOP_LINE = 19804  # 0-based line of the TaskStop on abce87a3bda959d98
REPLAY_STOP_ID = "toolu_01DEwwjwBNTxpbY1PY5RP7GR"


def typed(text: str) -> dict:
    return {"type": "user", "origin": {"kind": "human"}, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def use(uid: str, name: str, inp: dict) -> dict:
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]}}


def result(uid: str, text: str, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": uid, "content": [{"type": "text", "text": text}]}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"content": [block]}}


def launch(uid: str, agent: str, desc: str) -> list[dict]:
    return [
        use(uid, "Agent", {"description": desc, "prompt": "Do it.", "run_in_background": True}),
        result(uid, "Async agent launched successfully. (This tool result is internal metadata.)\n"
                    f"agentId: {agent} (internal ID - do not mention to user. Use SendMessage with to: "
                    f"'{agent}', summary: '<5-10 word recap>' to continue this agent.)\n"
                    "The agent is working in the background. You will be notified automatically when "
                    "it completes."),
    ]


def note(agent: str, desc: str, tail: str, status: str = "completed") -> dict:
    return {"type": "user", "message": {"content": (
        f"<task-notification>\n<task-id>{agent}</task-id>\n<status>{status}</status>\n"
        f"<summary>Agent \"{desc}\" {tail}</summary>\n<result>Report.</result>\n</task-notification>")}}


def waiting(agent: str, desc: str) -> dict:
    return note(agent, desc, "is waiting on its own background work")


def finished(agent: str, desc: str) -> dict:
    return note(agent, desc, "finished")


def bash_bg(uid: str, task: str) -> list[dict]:
    return [use(uid, "Bash", {"command": "python3 scripts/long-selftest.py", "run_in_background": True}),
            result(uid, f"Command running in background with ID: {task}. Output is being written to: "
                        f"/tmp/x/{task}.output")]


def resume(uid: str, agent: str) -> list[dict]:
    return [use(uid, "SendMessage", {"to": agent, "message": "One more pass."}),
            result(uid, f'{{"success":true,"message":"Resuming agent {agent[:7]}",'
                        f'"resumedAgentId":"{agent}"}}')]


def stop_call(uid: str, task: str) -> dict:
    return use(uid, "TaskStop", {"task_id": task})


def hook_refused(uid: str, task: str) -> list[dict]:
    return [stop_call(uid, task),
            result(uid, "PreToolUse:TaskStop hook error: Agent is not finished.", True)]


def chained(events: list[dict]) -> list[dict]:
    """Give every event a uuid and point it at the one before, as Claude Code's chain does."""
    out = []
    for i, ev in enumerate(events):
        out.append({**ev, "uuid": f"e{i}", "parentUuid": f"e{i - 1}" if i else None,
                    "isSidechain": False})
    return out


def run_signal(events: list[dict], task: str, extra: dict | None = None) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in chained(events)), encoding="utf-8")
        event = {"session_id": "s", "transcript_path": str(path), "cwd": str(REPO_ROOT),
                 "hook_event_name": "PreToolUse", "tool_name": "TaskStop",
                 "tool_input": {"task_id": task}, "tool_use_id": STOP_ID, **(extra or {})}
        return run_event(event)


def run_event(event: dict) -> str:
    proc = subprocess.run(["bash", str(SIGNAL)], input=json.dumps(event), cwd=REPO_ROOT, text=True,
                          capture_output=True, timeout=25)
    return proc.stdout.strip()


DENY, OVERRIDE, ALLOW = "deny", "override", "allow"


def verdict(out: str) -> str:
    if out.startswith("WAITINGAGENT|") and "|override=0" in out:
        return DENY
    if out.startswith("WAITINGAGENT|") and "|override=1" in out:
        return OVERRIDE
    return ALLOW if out == "" else f"odd:{out[:80]!r}"


GO = typed("Strip the losing rules and run the selftest.")
BASE = [GO, *launch("l1", A1, D1)]

# (name, events, task id, verdict, substrings the output must carry, extra event fields)
CASES = [
    ("waiting, then TaskStop on it", [*BASE, waiting(A1, D1)], A1, DENY, (A1, D1), None),
    ("finished, then TaskStop on it", [*BASE, finished(A1, D1)], A1, ALLOW, (), None),
    ("waiting, then finished, then TaskStop", [*BASE, waiting(A1, D1), finished(A1, D1)], A1, ALLOW,
     (), None),
    ("a bash task id while an agent waits", [*BASE, *bash_bg("b1", "b7rzimvi4"), waiting(A1, D1)],
     "b7rzimvi4", ALLOW, (), None),
    ("user's last message names the agent id", [*BASE, waiting(A1, D1), typed(f"stop {A1}")], A1,
     OVERRIDE, (), None),
    ("user's last message names a 7-character prefix",
     [*BASE, waiting(A1, D1), typed("abce87a can go, the selftest is moot")], A1, OVERRIDE, (), None),
    ("user's last message asks to stop the agent",
     [*BASE, waiting(A1, D1), typed("Cancel the subagent, I'll rerun it myself.")], A1, OVERRIDE, (),
     None),
    ("user's last message is about something else", [*BASE, waiting(A1, D1), typed("close finished subagents")],
     A1, DENY, (), None),
    ("an earlier message named it, the latest does not",
     [*BASE, typed(f"stop {A1}"), waiting(A1, D1), typed("keep going")], A1, DENY, (), None),
    ("the TaskStop's own line already on disk", [*BASE, waiting(A1, D1), stop_call(STOP_ID, A1)], A1,
     DENY, (A1,), None),
    ("a retry after a hook refused the first TaskStop",
     [*BASE, waiting(A1, D1), *hook_refused("s0", A1)], A1, DENY, (A1,), None),
    ("waiting, then resumed by SendMessage (running)", [*BASE, waiting(A1, D1), *resume("r1", A1)], A1,
     ALLOW, (), None),
    ("stopped with the user's word, killed notification, stopped again",
     [*BASE, waiting(A1, D1), stop_call("s0", A1), result("s0", '{"message":"Successfully stopped"}'),
      note(A1, D1, "was stopped by Claude", "killed")], A1, ALLOW, (), None),
    ("two agents: the finished one may be stopped while the other waits",
     [*BASE, *launch("l2", A2, D2), waiting(A1, D1), finished(A2, D2)], A2, ALLOW, (), None),
    ("two agents: the waiting one is refused", [*BASE, *launch("l2", A2, D2), waiting(A1, D1),
                                                finished(A2, D2)], A1, DENY, (A1,), None),
    ("an id nobody launched", [*BASE, waiting(A1, D1)], "a0000000000000000", ALLOW, (), None),
    ("a subagent's own TaskStop is not judged", [*BASE, waiting(A1, D1)], A1, ALLOW, (),
     {"agent_id": "a1111111111111111"}),
    ("the deprecated shell_id names the agent", [*BASE, waiting(A1, D1)], A1, DENY, (A1,),
     {"tool_input": {"shell_id": A1}}),
]


def replay_cases() -> list[tuple[str, str, str]]:
    """(name, verdict wanted, output) for the real transcript, or [] when it is not here."""
    if not REPLAY.is_file():
        return []
    with REPLAY.open(encoding="utf-8", errors="replace") as fh:
        lines = [next(fh) for _ in range(REPLAY_STOP_LINE + 1)]
    if REPLAY_STOP_ID not in lines[REPLAY_STOP_LINE]:
        return [("replay: transcript line 19805 is not the TaskStop", DENY, "line moved")]
    out = []
    for label, keep in (("before the call's line lands", REPLAY_STOP_LINE),
                        ("with the call's own line on disk", REPLAY_STOP_LINE + 1)):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "5ec7b779.jsonl"
            path.write_text("".join(lines[:keep]), encoding="utf-8")
            got = run_event({"session_id": "5ec7b779-1950-45a6-84dd-ef5b2c3e9082",
                             "transcript_path": str(path), "cwd": "/home/banon/projects/er-mods-rs",
                             "hook_event_name": "PreToolUse", "tool_name": "TaskStop",
                             "tool_input": {"task_id": A1}, "tool_use_id": REPLAY_STOP_ID})
        out.append((f"replay of session 5ec7b779 at the TaskStop on {A1}, {label}", DENY, got))
    return out


def main() -> int:
    failures = 0
    total = 0
    for name, events, task, want, must, extra in CASES:
        total += 1
        out = run_signal(events, task, extra)
        got = verdict(out)
        problems = [] if got == want else [f"want {want}, got {got}"]
        problems += [f"output lacks {s!r}" for s in must if s not in out]
        if problems:
            failures += 1
            print(f"FAIL {name}: {'; '.join(problems)} ({out[:200]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    replays = replay_cases()
    if not replays:
        print(f"skip: replay cases ({REPLAY} not on this machine)")
    for name, want, out in replays:
        total += 1
        got = verdict(out)
        if got != want:
            failures += 1
            print(f"FAIL {name}: want {want}, got {got} ({out[:200]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}: {out[:160]}")
    if failures:
        print(f"test-task-stop-waiting-agent-signal: {failures} failure(s) of {total}", file=sys.stderr)
        return 1
    print(f"test-task-stop-waiting-agent-signal: OK ({total} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
