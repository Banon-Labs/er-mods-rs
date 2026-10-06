#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_ask_without_receiver`.

The signal emits `ASKNORECEIVER:<sentence>` when the closing prose asks the user for an action or
event and nothing armed would wake the agent when it happens; empty otherwise. Each case writes a
crafted transcript, hands its path to the signal on stdin the way cupcake does, and asserts the tag.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_ask_without_receiver.sh"

ASK = (
    "The close trace is attached and waiting. I need you to close the item list with B twice: "
    "once from our view 3, and once from a native view."
)


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


WATCHER = [
    use("w1", "Bash", {"command": "uv run --with frida python3 scripts/er-frida-watch.py "
                                  "--agent scripts/frida/x.js > /tmp/t.log", "run_in_background": True}),
    result("w1", "Command running in background with ID: bw1. Output is being written to: /tmp/t.log"),
]
MONITOR = [
    use("m1", "Monitor", {"command": "tail -n0 -F /tmp/t.log | grep --line-buffered close",
                          "description": "close events"}),
    result("m1", "Monitor started (task bm1, timeout 1800000ms). You will be notified on each event."),
]
UNTIL = [
    use("u1", "Bash", {"command": "until grep -q close /tmp/t.log; do sleep 1; done; tail -3 /tmp/t.log",
                       "run_in_background": True}),
    result("u1", "Command running in background with ID: bu1."),
]


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
    ("the 2026-10-02 closer, watcher only", [user("go"), *WATCHER, say(ASK)], HALT),
    ("same ask, live Monitor", [user("go"), *WATCHER, *MONITOR, say(ASK)], ALLOW),
    ("Monitor armed in an earlier turn, still live",
     [user("go"), *WATCHER, *MONITOR, say("Armed."), user("ok"), say(ASK)], ALLOW),
    ("Monitor whose stream ended", [user("go"), *MONITOR, notify("m1", "bm1"), say(ASK)], HALT),
    ("Monitor stopped by TaskStop",
     [user("go"), *MONITOR, use("s1", "TaskStop", {"task_id": "bm1"}), result("s1", "stopped"), say(ASK)],
     HALT),
    ("Monitor refused by a hook",
     [user("go"), use("m1", "Monitor", {"command": "tail -f /tmp/t.log"}),
      result("m1", "PreToolUse:Monitor hook error: blocked an unthrottled Monitor", True), say(ASK)],
     HALT),
    ("background until-loop still waiting", [user("go"), *WATCHER, *UNTIL, say(ASK)], ALLOW),
    ("background until-loop already exited",
     [user("go"), *UNTIL, notify("u1", "bu1"), say(ASK)], HALT),
    ("let me know when", [user("go"), say("Press R3 in the item list and let me know when it is open.")], HALT),
    ("once you", [user("go"), say("Once you open the inventory, the probe records the cadence.")], HALT),
    ("once you, after a comma", [user("go"), say("Open the item list, and once you do, the probe records it.")],
     HALT),
    ("first-person consequent, trailing when you",
     [user("go"), say("I'll read the close trace when you open the inventory.")], HALT),
    ("bulleted once you", [user("go"), say("- Once you invade, the log names the host.")], HALT),
    ("2026-10-04 false positive: game-mechanic after you, mid-sentence",
     [user("go"), say("The axe row is the best roll cover: the Hand Axe, followed by the Stone Club and "
                      "the Battle Axe group, which catch a roll taken at the first chance in 5 of 6 cases "
                      "after you run in.")], ALLOW),
    ("game-mechanic when you, mid-sentence",
     [user("go"), say("The Greatsword staggers a shield poke when you two-hand it.")], ALLOW),
    ("please verb",[user("go"), say("Please invade a host from the Bloodhound's Fang tile.")], HALT),
    ("subjective question", [user("go"), say("Does the font look right to you?")], ALLOW),
    ("subjective ask phrase", [user("go"), say("Please tell me if the board looks right.")], ALLOW),
    ("decision", [user("go"), say("I need you to decide whether the board replaces view 2 or adds a view 4.")],
     ALLOW),
    ("sudo", [user("go"), say("I need you to run the sudo command in the new Kitty tab.")], ALLOW),
    ("observation with no oracle",
     [user("go"), say("Tell me what you see on the loading screen; there is no pixel oracle for it.")], ALLOW),
    ("self need", [user("go"), say("I need to rebuild the shell before the next run.")], ALLOW),
    ("quoted ask", [user("go"), say('The bad closer was "I need you to close the item list".')], ALLOW),
    ("ask mid-turn, report at the end",
     [user("go"), say("I need you to close it."), use("e1", "Edit", {"file_path": "/x"}), result("e1", "ok"),
      say("Edited the trace agent.")], ALLOW),
]


def main() -> int:
    failures = 0
    for name, events, want in CASES:
        out = run_signal(events)
        got = HALT if out.startswith("ASKNORECEIVER:") else ALLOW if out == "" else f"odd:{out!r}"
        if got != want:
            failures += 1
            print(f"FAIL {name}: want {want}, got {got} ({out[:160]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    if failures:
        print(f"test-ask-without-receiver-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-ask-without-receiver-signal: OK ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
