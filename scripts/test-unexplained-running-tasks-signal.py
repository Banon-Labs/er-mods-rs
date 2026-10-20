#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_unexplained_running_tasks`.

The signal emits `UNEXPLAINEDTASKS:<id> (...)` when the turn ends while a background Bash or Monitor
task the session started is still running and the closing message does not name its id with a
reason; empty otherwise. Each case writes a crafted transcript, hands its path to the signal on stdin
the way cupcake does, and asserts the verdict.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_unexplained_running_tasks.sh"

REPORT = "Pushed the branch; the gate is green and the PR body carries the evidence."


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
    block = {"type": "tool_result", "tool_use_id": uid, "content": text}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"content": [block]}}


def notify(uid: str, task: str, status: str = "completed") -> dict:
    return user(
        f"<task-notification>\n<task-id>{task}</task-id>\n<tool-use-id>{uid}</tool-use-id>\n"
        f"<status>{status}</status>\n<summary>done</summary>\n</task-notification>"
    )


def monitor_expired(task: str) -> dict:
    return user(
        f"<task-notification>\n<task-id>{task}</task-id>\n<summary>Monitor event: \"x\"</summary>\n"
        "<event>[Monitor expired after 30m with 1 event delivered. Re-arm it if you still need the "
        "watch.]</event>\n</task-notification>"
    )


def queued_notify(uid: str, task: str) -> dict:
    return {
        "type": "attachment",
        "attachment": {
            "type": "queued_command",
            "prompt": f"<task-notification>\n<task-id>{task}</task-id>\n<tool-use-id>{uid}</tool-use-id>\n"
                      "<status>completed</status>\n</task-notification>",
        },
    }


COPIER = [
    use("c1", "Bash", {"command": "while true; do cp -f /run/stages/*.log /tmp/s/; sleep 2; done",
                       "run_in_background": True}),
    result("c1", "Command running in background with ID: b31ydejza. Output is being written to: /tmp/x"),
]
MOVED = [
    use("u1", "Bash", {"command": "until grep -q done /tmp/push.log; do read -t 3 <> <(:) || true; done"}),
    result("u1", "Command did not complete within its 120s timeout and was moved to the background "
                 "(ID: bd4o7fhhg). Output is being written to: /tmp/y. You will be notified when it completes."),
]
MONITOR = [
    use("m1", "Monitor", {"command": "tail -n0 -F /tmp/t.log | grep --line-buffered close",
                          "description": "close events"}),
    result("m1", "Monitor started (task bp6zx2fcc, expires in 30m unless the source ends first). "
                 "You will be notified on each event."),
]


def stop(uid: str, task: str, ok: bool = True) -> list[dict]:
    text = (f'{{"message":"Successfully stopped task: {task} (...)"}}' if ok
            else f"No task found with ID: {task}")
    return [use(uid, "TaskStop", {"task_id": task}), result(uid, text, not ok)]


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

CASES = [
    ("nothing backgrounded", [user("go"), say(REPORT)], ALLOW),
    # 2026-10-19: a foreground grep that printed test fixtures quoting launch acknowledgements booked
    # their ids (bx1, bd12ab) as tasks this session started.
    ("output quoting a launch acknowledgement is not a launch",
     [user("go"),
      use("g1", "Bash", {"command": "grep -rn 'running in background' scripts/"}),
      result("g1", 'scripts/test-x.py:374: "content": "Command running in background with ID: bx1",\n'
                   'scripts/test-y.py:171: "content": "Command running in background with ID: bd12ab. "'),
      say(REPORT)], ALLOW),
    ("the 2026-10-07 shape: copier and waiter left running, report ignores them",
     [user("go"), *COPIER, *MOVED, say(REPORT)], HALT),
    ("running task not mentioned", [user("go"), *COPIER, say(REPORT)], HALT),
    ("running Monitor not mentioned", [user("go"), *MONITOR, say(REPORT)], HALT),
    ("task stopped", [user("go"), *COPIER, *stop("s1", "b31ydejza"), say(REPORT)], ALLOW),
    ("task stop refused because it already ended",
     [user("go"), *COPIER, *stop("s1", "b31ydejza", ok=False), say(REPORT)], ALLOW),
    ("Monitor stopped", [user("go"), *MONITOR, *stop("s1", "bp6zx2fcc"), say(REPORT)], ALLOW),
    ("task completed (user-event notification)",
     [user("go"), *COPIER, notify("c1", "b31ydejza"), say(REPORT)], ALLOW),
    ("task failed counts as finished",
     [user("go"), *MOVED, notify("u1", "bd4o7fhhg", "failed"), say(REPORT)], ALLOW),
    ("task completed mid-turn (queued_command attachment)",
     [user("go"), *COPIER, queued_notify("c1", "b31ydejza"), say(REPORT)], ALLOW),
    ("Monitor expired", [user("go"), *MONITOR, monitor_expired("bp6zx2fcc"), say(REPORT)], ALLOW),
    ("running task justified by id in the closing message",
     [user("go"), *MONITOR,
      say("Monitor `bp6zx2fcc` stays armed: it is the receiver for the close events the trace waits on.")],
     ALLOW),
    ("one of two justified, the other not",
     [user("go"), *MONITOR, *COPIER,
      say("Monitor bp6zx2fcc stays armed because it receives the close events from the trace.")], HALT),
    ("both justified on separate lines",
     [user("go"), *MONITOR, *COPIER,
      say("- bp6zx2fcc: still receiving close events from the trace\n"
          "- b31ydejza: still copying stage logs until the suite finishes")], ALLOW),
    ("bare id list is not a reason",
     [user("go"), *MONITOR, *COPIER, say("Still running: bp6zx2fcc b31ydejza.")], HALT),
    ("id named in an earlier prose run, not the closing one",
     [user("go"), *COPIER, say("Copier b31ydejza is mirroring the stage logs for the push."),
      use("e1", "Edit", {"file_path": "/x"}), result("e1", "ok"), say(REPORT)], HALT),
    ("launch refused by a hook",
     [user("go"), use("m1", "Monitor", {"command": "tail -f /tmp/t.log"}),
      result("m1", "PreToolUse:Monitor hook error: blocked an unthrottled Monitor", True), say(REPORT)], ALLOW),
    ("foreground Bash that finished", [user("go"), use("f1", "Bash", {"command": "ls"}), result("f1", "a b"),
                                       say(REPORT)], ALLOW),
    ("started in an earlier turn, still running, unmentioned",
     [user("go"), *COPIER, say("Copier up."), user("status?"), say(REPORT)], HALT),
]


def main() -> int:
    failures = 0
    for name, events, want in CASES:
        out = run_signal(events)
        got = HALT if out.startswith("UNEXPLAINEDTASKS:") else ALLOW if out == "" else f"odd:{out!r}"
        if got != want:
            failures += 1
            print(f"FAIL {name}: want {want}, got {got} ({out[:160]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    if failures:
        print(f"test-unexplained-running-tasks-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-unexplained-running-tasks-signal: OK ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
