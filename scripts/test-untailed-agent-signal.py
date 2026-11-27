#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_untailed_agent`.

The signal emits `UNTAILEDAGENT:<id> ("<description>") => python3 scripts/kitty-tail-agent.py <id>`
when the turn being stopped started a background subagent (an Agent launch, or a SendMessage that
resumed one) that is still working, and no later call opened a kitty tab following its transcript;
empty otherwise. Each case writes a crafted transcript, hands its path to the signal on stdin the
way cupcake does, and asserts the verdict. Replaying real transcripts is
`python3 scripts/cupcake_untailed_agent.py --replay [N]`.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_untailed_agent.sh"

ROUND2 = "a505613c401ed9d43"
OTHER = "a740eb3ea960d3d6b"
DESC = "Speed up attack sets, round 2"
REPORT = "Round 2 of the attack-set speedup is running in the background."


def user(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


def say(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def use(uid: str, name: str, inp: dict) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]},
    }


def result(uid: str, text: str, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": uid, "content": [{"type": "text", "text": text}]}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"content": [block]}}


def launch(uid: str, agent: str = ROUND2, desc: str = DESC) -> list[dict]:
    """A background Agent call and the harness's acknowledgement, verbatim in shape (session
    5ec7b779, 2026-11-11)."""
    return [
        use(uid, "Agent", {"description": desc, "prompt": "Make the attack-set stage faster.",
                           "run_in_background": True}),
        result(uid, "Async agent launched successfully. (This tool result is internal metadata.)\n"
                    f"agentId: {agent} (internal ID - do not mention to user. Use SendMessage with to: "
                    f"'{agent}', summary: '<5-10 word recap>' to continue this agent.)\n"
                    "The agent is working in the background. You will be notified automatically when it completes."),
    ]


def foreground(uid: str, agent: str = ROUND2) -> list[dict]:
    return [
        use(uid, "Agent", {"description": DESC, "prompt": "Survey it."}),
        result(uid, f"The survey is done: 14 sets.\nagentId: {agent} (for resuming)"),
    ]


def tail(uid: str, cmd: str, ok: bool = True) -> list[dict]:
    text = f"tab 'agent: {DESC}' (window 7) follows ..." if ok else "Exit code 1\nKITTY_LISTEN_ON is not set"
    return [use(uid, "Bash", {"command": cmd}), result(uid, text, not ok)]


def notify(agent: str, verb: str, mid_turn: bool = True) -> dict:
    body = (f"<task-notification>\n<task-id>{agent}</task-id>\n<status>completed</status>\n"
            f"<summary>Agent \"{DESC}\" {verb}</summary>\n</task-notification>")
    if mid_turn:
        return {"type": "attachment", "attachment": {"type": "queued_command", "prompt": body}}
    return {"type": "user", "origin": {"kind": "task-notification"}, "message": {"content": body}}


def stop_feedback(text: str = "Stop hook feedback:\nThis turn started a background subagent ...") -> dict:
    return {"type": "user", "isMeta": True, "message": {"content": text}}


def resume(uid: str, agent: str = ROUND2) -> list[dict]:
    return [
        use(uid, "SendMessage", {"to": agent, "message": "Bring the stage back under 2 s."}),
        result(uid, json.dumps({"success": True, "message": f"Resuming agent {agent[:7]}",
                                "resumedAgentId": agent})),
    ]


TAB = f"python3 scripts/kitty-tail-agent.py {ROUND2}"
# A turn that launched the agent, followed it and saw it finish: what a later resume starts again.
EARLIER_ROUND = [user("speed it up"), *launch("g0"), *tail("t0", TAB), notify(ROUND2, "finished"),
                 say("Round 2 finished.")]


def run_signal(events: list[dict]) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        proc = subprocess.run(
            ["bash", str(SIGNAL)],
            input=json.dumps({"transcript_path": str(path), "hook_event_name": "Stop"}),
            cwd=REPO_ROOT, text=True, capture_output=True, timeout=25,
        )
        return proc.stdout.strip()


HALT = "halt"
ALLOW = "allow"

# (name, events, verdict, substrings the halt must carry)
CASES = [
    ("the 2026-11-11 shape: a background agent launched, no tab",
     [user("speed it up again"), *launch("g1"), say(REPORT)], HALT,
     [f'{ROUND2} ("{DESC}")', f"=> python3 scripts/kitty-tail-agent.py {ROUND2}"]),
    ("the agent followed in a tab after its launch",
     [user("go"), *launch("g1"), *tail("t1", TAB), say(REPORT)], ALLOW, []),
    ("the absolute spelling of the tab command",
     [user("go"), *launch("g1"),
      *tail("t1", f"python3 /home/banon/projects/er-mods-rs/scripts/kitty-tail-agent.py {ROUND2}"),
      say(REPORT)], ALLOW, []),
    ("a tab for another agent does not count",
     [user("go"), *launch("g1"), *tail("t1", f"python3 scripts/kitty-tail-agent.py {OTHER}"), say(REPORT)],
     HALT, [ROUND2]),
    ("--dry-run opens nothing",
     [user("go"), *launch("g1"), *tail("t1", f"{TAB} --dry-run"), say(REPORT)], HALT, [ROUND2]),
    ("reading the script with the id in the command opens nothing",
     [user("go"), *launch("g1"), *tail("t1", f"grep -n {ROUND2} scripts/kitty-tail-agent.py"), say(REPORT)],
     HALT, [ROUND2]),
    ("two agents, one tab: the halt names only the other",
     [user("go"), *launch("g1"), *launch("g2", OTHER, "Speed up attack-set stage"), *tail("t1", TAB),
      say(REPORT)], HALT, [OTHER, f"kitty-tail-agent.py {OTHER}"]),
    ("two agents in one tab command",
     [user("go"), *launch("g1"), *launch("g2", OTHER), *tail("t1", f"{TAB} {OTHER}"), say(REPORT)], ALLOW, []),
    ("a foreground agent returned its whole result",
     [user("go"), *foreground("g1"), say(REPORT)], ALLOW, []),
    ("the agent finished inside the turn",
     [user("go"), *launch("g1"), notify(ROUND2, "finished"), say(REPORT)], ALLOW, []),
    ("the agent was stopped inside the turn",
     [user("go"), *launch("g1"), use("s1", "TaskStop", {"task_id": ROUND2}), result("s1", "stopped"),
      say(REPORT)], ALLOW, []),
    ("waiting on its own background work is still working",
     [user("go"), *launch("g1"), notify(ROUND2, "is waiting on its own background work"), say(REPORT)],
     HALT, [ROUND2]),
    ("launched in an earlier turn the user typed",
     [user("go"), *launch("g1"), say(REPORT), user("status?"), say(REPORT)], ALLOW, []),
    ("a task notification delivered as a prompt starts a new turn",
     [user("go"), *launch("g1"), say(REPORT), notify(OTHER, "finished", mid_turn=False), say(REPORT)],
     ALLOW, []),
    ("a halt not acted on fires again: Stop hook feedback does not end the turn",
     [user("go"), *launch("g1"), say(REPORT), stop_feedback(), say(REPORT)], HALT, [ROUND2]),
    ("a halt acted on: the tab opened in the continuation",
     [user("go"), *launch("g1"), say(REPORT), stop_feedback(), *tail("t1", TAB), say(REPORT)], ALLOW, []),
    ("a failed tab call reported in the closing message",
     [user("go"), *launch("g1"), *tail("t1", TAB, ok=False),
      say("The kitty tab did not open: KITTY_LISTEN_ON is not set in this shell.")], ALLOW, []),
    ("a failed tab call not reported",
     [user("go"), *launch("g1"), *tail("t1", TAB, ok=False), say(REPORT)], HALT, [ROUND2]),
    ("a resumed agent with no tab",
     [*EARLIER_ROUND, user("one more round"), *resume("r1"), say(REPORT)], HALT,
     [ROUND2, f"kitty-tail-agent.py {ROUND2}"]),
    ("a resumed agent followed after the resume",
     [*EARLIER_ROUND, user("one more round"), *resume("r1"), *tail("t1", TAB), say(REPORT)], ALLOW, []),
    ("an agent the transcript never launched is not judged",
     [user("one more round"), *resume("r1"), say(REPORT)], ALLOW, []),
    ("a tab from before the resume does not count",
     [user("go"), *launch("g1"), *tail("t1", TAB), notify(ROUND2, "finished"), *resume("r1"), say(REPORT)],
     HALT, [ROUND2]),
    ("a launch refused by a hook",
     [user("go"), use("g1", "Agent", {"description": DESC, "prompt": "x", "run_in_background": True}),
      result("g1", "PreToolUse:Agent hook error: refused", True), say(REPORT)], ALLOW, []),
]


def main() -> int:
    failures = 0
    for name, events, want, needles in CASES:
        out = run_signal(events)
        got = HALT if out.startswith("UNTAILEDAGENT:") else ALLOW if out == "" else f"odd:{out!r}"
        missing = [n for n in needles if n not in out]
        if got != want or missing:
            failures += 1
            print(f"FAIL {name}: want {want}, got {got}; missing {missing} ({out[:240]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    if failures:
        print(f"test-untailed-agent-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-untailed-agent-signal: OK ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
