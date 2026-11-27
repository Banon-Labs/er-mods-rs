#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_unclosed_finished_agents`.

The signal emits `UNCLOSEDAGENTS:<agent id> ("<description>"); ...` when the turn ends while a
subagent the main session launched has finished and no TaskStop has named it since; empty otherwise.
User directive 2026-11-11, one quoted line at a time:
  "If I ever see a subagent that is finished but hasn't been closed, I always have VERY HIGH"
  "concerns that you consumed its results."

Each case writes a crafted transcript, hands its path to the signal on stdin the way cupcake does, and
asserts the verdict and, for a halt, which agent ids the output names and which it must not. The
launch, notification, resume and stop texts are the harness's own, copied from session 5ec7b779.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_unclosed_finished_agents.sh"

REPORT = "The release model is in the attacks stage, and its selftests are the agent's own five."
A1 = "a4d14f2d4d71a5ba0"
A2 = "ac34f26adeea54127"


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


def launch(uid: str, agent: str, desc: str) -> list[dict]:
    return [
        use(uid, "Agent", {"description": desc, "prompt": "Do it.", "run_in_background": True}),
        result(uid, "Async agent launched successfully. (This tool result is internal metadata.)\n"
                    f"agentId: {agent} (internal ID - do not mention to user. Use SendMessage with to: "
                    f"'{agent}', summary: '<5-10 word recap>' to continue this agent.)\n"
                    "The agent is working in the background. You will be notified automatically when "
                    "it completes."),
    ]


def note(agent: str, desc: str, tail: str = "finished", status: str = "completed") -> str:
    return (f"<task-notification>\n<task-id>{agent}</task-id>\n<output-file>/tmp/x/{agent}.output"
            f"</output-file>\n<status>{status}</status>\n<summary>Agent \"{desc}\" {tail}</summary>\n"
            "<note>A task-notification fires each time this agent stops with no live background "
            "children of its own.</note>\n<result>Done; the changed files are listed below.</result>\n"
            "</task-notification>")


def finished(agent: str, desc: str) -> dict:
    return user(note(agent, desc))


def waiting(agent: str, desc: str) -> dict:
    return user(note(agent, desc, "is waiting on its own background work"))


def queued(agent: str, desc: str) -> dict:
    return {"type": "attachment", "attachment": {"type": "queued_command", "prompt": note(agent, desc)}}


def enqueued(agent: str, desc: str) -> dict:
    return {"type": "queue-operation", "operation": "enqueue", "content": note(agent, desc)}


def blob(agent: str, desc: str) -> dict:
    return {"type": "api-request-blob", "message": {"role": "user", "content": note(agent, desc)}}


def stop(uid: str, agent: str, outcome: str = "completed") -> list[dict]:
    text = {
        "completed": f"<tool_use_error>Task {agent} is not running (status: completed)</tool_use_error>",
        "missing": f"<tool_use_error>No task found with ID: {agent}.</tool_use_error>",
        "ok": f'{{"message":"Successfully stopped task: {agent} (agent)"}}',
    }[outcome]
    return [use(uid, "TaskStop", {"task_id": agent}), result(uid, text, outcome != "ok")]


def resume(uid: str, agent: str, ok: bool = True) -> list[dict]:
    text = (f'{{"success":true,"message":"Resuming agent {agent[:7]}","resumedAgentId":"{agent}",'
            f'"pin":{{"id":"{agent}","name":"{agent}","ref":"f20d54"}}}}' if ok
            else f"<tool_use_error>No agent found with ID: {agent}</tool_use_error>")
    return [use(uid, "SendMessage", {"to": agent, "message": "One more pass."}), result(uid, text, not ok)]


def queued_message(uid: str, agent: str) -> list[dict]:
    text = (f'{{"success":true,"message":"Message queued for delivery to {agent} at its next tool '
            'round. If it finishes first, it is resumed with the message and notifies again, so wait '
            'for that notification before resending."}')
    return [use(uid, "SendMessage", {"to": agent, "message": "Also this."}), result(uid, text)]


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

D1 = "Model release frame unknown to defender"
D2 = "Fix wall-of-text count of reply line"

# (name, events, verdict, ids the output must name, ids it must not name)
CASES = [
    ("nothing launched", [user("go"), say(REPORT)], ALLOW, (), ()),
    ("finished then closed (TaskStop refused: not running)",
     [user("go"), *launch("l1", A1, D1), finished(A1, D1), *stop("s1", A1), say(REPORT)], ALLOW, (), ()),
    ("finished then closed (TaskStop refused: no task found)",
     [user("go"), *launch("l1", A1, D1), finished(A1, D1), *stop("s1", A1, "missing"), say(REPORT)],
     ALLOW, (), ()),
    ("finished, not closed",
     [user("go"), *launch("l1", A1, D1), finished(A1, D1), say(REPORT)], HALT, (A1, D1), ()),
    ("waiting on its own background work is not finished",
     [user("go"), *launch("l1", A1, D1), waiting(A1, D1), say(REPORT)], ALLOW, (), ()),
    ("finished, resumed by SendMessage, still running",
     [user("go"), *launch("l1", A1, D1), finished(A1, D1), *resume("r1", A1), say(REPORT)], ALLOW, (), ()),
    ("finished, resumed, finished again, closed only before the resume",
     [user("go"), *launch("l1", A1, D1), finished(A1, D1), *stop("s1", A1), *resume("r1", A1),
      finished(A1, D1), say(REPORT)], HALT, (A1,), ()),
    ("finished, resumed, finished again, closed after the second finish",
     [user("go"), *launch("l1", A1, D1), finished(A1, D1), *resume("r1", A1), finished(A1, D1),
      *stop("s1", A1), say(REPORT)], ALLOW, (), ()),
    ("two agents, one closed and one not: names only the open one",
     [user("go"), *launch("l1", A1, D1), *launch("l2", A2, D2), finished(A1, D1), finished(A2, D2),
      *stop("s2", A2, "missing"), say(REPORT)], HALT, (A1, D1), (A2, D2)),
    ("still running, no notification yet",
     [user("go"), *launch("l1", A1, D1), say("The agent is modelling the release frame.")], ALLOW, (), ()),
    ("finished in an earlier turn and never closed",
     [user("go"), *launch("l1", A1, D1), finished(A1, D1), say(REPORT), user("and the docs?"),
      say("The docs say the same.")], HALT, (A1,), ()),
    ("finished notification delivered mid-turn as a queued_command attachment",
     [user("go"), *launch("l1", A1, D1), queued(A1, D1), say(REPORT)], HALT, (A1,), ()),
    ("waiting, then finished on its own",
     [user("go"), *launch("l1", A1, D1), waiting(A1, D1), finished(A1, D1), say(REPORT)], HALT, (A1,), ()),
    ("finished, then waiting again after a resume is not finished",
     [user("go"), *launch("l1", A1, D1), finished(A1, D1), *resume("r1", A1), waiting(A1, D1),
      say(REPORT)], ALLOW, (), ()),
    ("a refused SendMessage does not resume it",
     [user("go"), *launch("l1", A1, D1), finished(A1, D1), *resume("r1", A1, ok=False), say(REPORT)],
     HALT, (A1,), ()),
    ("a message queued to a running agent reopens nothing; its finish still counts",
     [user("go"), *launch("l1", A1, D1), *queued_message("q1", A1), finished(A1, D1), say(REPORT)],
     HALT, (A1,), ()),
    ("stopped while running, then the killed notification",
     [user("go"), *launch("l1", A1, D1), *stop("s1", A1, "ok"),
      user(note(A1, D1, "was stopped by Claude", "killed")), say(REPORT)], ALLOW, (), ()),
    ("an agent a subagent launched: notification with no launch here",
     [user("go"), enqueued("a8d369a23e434bf65", "Survey charged skill AtkParams"),
      {"type": "queue-operation", "operation": "remove",
       "content": note("a8d369a23e434bf65", "Survey charged skill AtkParams")}, say(REPORT)], ALLOW, (), ()),
    ("a request copy of the finished notification after the TaskStop",
     [user("go"), *launch("l1", A1, D1), finished(A1, D1), *stop("s1", A1), blob(A1, D1), say(REPORT)],
     ALLOW, (), ()),
    ("a sidechain launch and finish are not the main session's",
     [user("go"), *[dict(e, isSidechain=True) for e in launch("l1", A1, D1)],
      dict(finished(A1, D1), isSidechain=True), say(REPORT)], ALLOW, (), ()),
    ("launch refused by a hook",
     [user("go"), use("l1", "Agent", {"description": D1, "prompt": "x", "run_in_background": True}),
      result("l1", "PreToolUse:Agent hook error: refused", True), say(REPORT)], ALLOW, (), ()),
    ("a launch with no agentId (older fixture shape) is not tracked",
     [user("go"), use("l1", "Agent", {"description": D1, "prompt": "x"}),
      result("l1", "Async agent launched successfully. You will be notified when it completes."),
      user("<task-notification><tool-use-id>l1</tool-use-id><status>completed</status>the agent "
           "finished</task-notification>"), say(REPORT)], ALLOW, (), ()),
    ("notification quoted in the agent's own prose is not a notification",
     [user("go"), *launch("l1", A1, D1), say("A finished one reads: " + note(A1, D1))], ALLOW, (), ()),
]


def main() -> int:
    failures = 0
    for name, events, want, must, must_not in CASES:
        out = run_signal(events)
        got = HALT if out.startswith("UNCLOSEDAGENTS:") else ALLOW if out == "" else f"odd:{out!r}"
        problems = []
        if got != want:
            problems.append(f"want {want}, got {got}")
        problems += [f"output lacks {s!r}" for s in must if s not in out]
        problems += [f"output names {s!r}" for s in must_not if s in out]
        if problems:
            failures += 1
            print(f"FAIL {name}: {'; '.join(problems)} ({out[:200]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    if failures:
        print(f"test-unclosed-finished-agents-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-unclosed-finished-agents-signal: OK ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
