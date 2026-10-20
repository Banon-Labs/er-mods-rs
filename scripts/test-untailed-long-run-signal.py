#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_untailed_long_run`.

The signal emits `UNTAILEDRUN:<command> => <tail command>` when the turn being stopped sent a long
run to the background (board regeneration, a weapon or DLL build, the check suite, a push, ...)
and no later call in the same turn opened a kitty tab tailing its real output; empty otherwise.
Each case writes a crafted transcript, hands its path to the signal on stdin the way cupcake does,
and asserts the verdict.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_untailed_long_run.sh"

REPORT = "The board regeneration is running; it resumes from the cache if it is stopped."
OUT = "/tmp/claude-1000/x/tasks/bq1.output"


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


def bg(uid: str, cmd: str, task: str = "bq1") -> list[dict]:
    return [
        use(uid, "Bash", {"command": cmd, "run_in_background": True}),
        result(uid, f"Command running in background with ID: {task}. Output is being written to: "
                    f"/tmp/claude-1000/x/tasks/{task}.output. You will be notified when it completes."),
    ]


def moved(uid: str, cmd: str, task: str = "bq1") -> list[dict]:
    return [
        use(uid, "Bash", {"command": cmd}),
        result(uid, f"Command did not complete within its 120s timeout and was moved to the background "
                    f"(ID: {task}). Output is being written to: /tmp/claude-1000/x/tasks/{task}.output. "
                    "You will be notified when it completes."),
    ]


def tail(uid: str, cmd: str, ok: bool = True) -> list[dict]:
    text = "tab 'boards' (window 7) tails ..." if ok else "Exit code 1\nKITTY_LISTEN_ON is unset"
    return [use(uid, "Bash", {"command": cmd}), result(uid, text, not ok)]


def notify(uid: str, task: str) -> dict:
    return {
        "type": "attachment",
        "attachment": {
            "type": "queued_command",
            "prompt": f"<task-notification>\n<task-id>{task}</task-id>\n<tool-use-id>{uid}</tool-use-id>\n"
                      "<status>completed</status>\n</task-notification>",
        },
    }


BOARDS = "python3 scripts/gen-r3-weapon-boards.py --jobs 3"
BOARDS_TAB = f"python3 scripts/kitty-tail-task.py --boards --title boards --file {OUT}"
WRAPPER_ONLY_TAB = f"python3 scripts/kitty-tail-task.py --title boards --file {OUT} --match gen-r3-weapon-boards.py"


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
    ("the 2026-10-19 shape: board regeneration backgrounded, no tab",
     [user("regenerate the boards"), *bg("b1", BOARDS), say(REPORT)], HALT,
     [BOARDS, "kitty-tail-task.py --boards", OUT]),
    ("board regeneration moved to the background, no tab",
     [user("regenerate the boards"), *moved("b1", BOARDS), say(REPORT)], HALT, ["--boards"]),
    ("board regeneration with its own redirect suggests that file",
     [user("go"), *bg("b1", f"{BOARDS} > /tmp/s/boards.log 2>&1"), say(REPORT)], HALT,
     ["--file /tmp/s/boards.log"]),
    ("a redirect through a variable the command assigns is spelled out",
     [user("go"), *bg("b1", f"S=/tmp/s; {BOARDS} > $S/boards.log 2>&1; echo done > $S/flag"), say(REPORT)],
     HALT, ["--file /tmp/s/boards.log"]),
    ("stdout alone redirected leaves the task output file as the one to follow",
     [user("go"), *bg("b1", "python3 scripts/er-builds-pvp.py --rl 150 > /tmp/s/rank.json"), say(REPORT)],
     HALT, ["--file /tmp/claude-1000/x/tasks/bq1.output"]),
    ("a python one-liner that opens the script is not a run of it",
     [user("go"), *bg("b1", "python3 -c \"print(open('scripts/er-builds-pvp.py').read()[:9])\""), say(REPORT)],
     ALLOW, []),
    ("er-weapon-build.py --dry-run is not a long run",
     [user("go"), *bg("b1", "python3 scripts/er-weapon-build.py Uchigatana --dry-run"), say(REPORT)], ALLOW, []),
    ("board regeneration tailed with --boards",
     [user("go"), *bg("b1", BOARDS), *tail("t1", BOARDS_TAB), say(REPORT)], ALLOW, []),
    ("board regeneration tailed with one --file per shard log",
     [user("go"), *bg("b1", BOARDS),
      *tail("t1", f"python3 scripts/kitty-tail-task.py --file {OUT} --file /c/shard-0.log "
                  "--file /c/shard-1.log --pid 4242"), say(REPORT)], ALLOW, []),
    ("board regeneration tailed on the wrapper output only does not count",
     [user("go"), *bg("b1", BOARDS), *tail("t1", WRAPPER_ONLY_TAB), say(REPORT)], HALT, ["--boards"]),
    ("tab before the launch does not count",
     [user("go"), *tail("t1", BOARDS_TAB), *bg("b1", BOARDS), say(REPORT)], HALT, []),
    ("gen-r3-weapon-boards.py --check is not a long run",
     [user("go"), *bg("b1", "python3 scripts/gen-r3-weapon-boards.py --check"), say(REPORT)], ALLOW, []),
    ("gen-r3-weapon-boards.py --selftest is not a long run",
     [user("go"), *bg("b1", "python3 scripts/gen-r3-weapon-boards.py --selftest"), say(REPORT)], ALLOW, []),
    ("unrelated background command",
     [user("go"), *bg("b1", "sleep 600; ls /tmp"), say(REPORT)], ALLOW, []),
    ("grep for the script name is not a run of it",
     [user("go"), *bg("b1", "grep -rn gen-r3-weapon-boards.py scripts/"), say(REPORT)], ALLOW, []),
    ("foreground long run that finished is not backgrounded",
     [user("go"), use("f1", "Bash", {"command": BOARDS}), result("f1", "gen-r3-weapon-boards: done"),
      say(REPORT)], ALLOW, []),
    ("launched in an earlier turn",
     [user("go"), *bg("b1", BOARDS), say("Started."), user("status?"), say(REPORT)], ALLOW, []),
    ("run already finished inside the turn",
     [user("go"), *bg("b1", BOARDS), notify("b1", "bq1"), say(REPORT)], ALLOW, []),
    ("weapon build without a tab",
     [user("go"), *bg("b1", "FORCE_COLOR=1 python3 scripts/er-weapon-build.py --fight duel"), say(REPORT)],
     HALT, ["--match er-weapon-build.py"]),
    ("weapon build tailed",
     [user("go"), *bg("b1", "FORCE_COLOR=1 python3 scripts/er-weapon-build.py --fight duel"),
      *tail("t1", f"python3 scripts/kitty-tail-task.py --title duel --file {OUT} --match er-weapon-build.py"),
      say(REPORT)], ALLOW, []),
    ("git push without a tab suggests kitty-tail-push.py",
     [user("authorize abc1234"), *bg("p1", "git push -u origin feat/x"), say(REPORT)], HALT,
     ["kitty-tail-push.py --log"]),
    ("git push tailed with kitty-tail-push.py",
     [user("go"), *bg("p1", "git -C /r push origin feat/x"),
      *tail("t1", f"python3 scripts/kitty-tail-push.py --log {OUT}"), say(REPORT)], ALLOW, []),
    ("cargo xwin build without a tab",
     [user("go"), *bg("b1", "cargo xwin build --release --target x86_64-pc-windows-msvc -p er-x"),
      say(REPORT)], HALT, ["--match xwin"]),
    ("failed tab call reported in the closing message",
     [user("go"), *bg("b1", BOARDS), *tail("t1", BOARDS_TAB, ok=False),
      say("The kitty tab did not open: KITTY_LISTEN_ON is unset in this shell.")], ALLOW, []),
    ("failed tab call not reported",
     [user("go"), *bg("b1", BOARDS), *tail("t1", BOARDS_TAB, ok=False), say(REPORT)], HALT, []),
    ("launch refused by a hook",
     [user("go"), use("b1", "Bash", {"command": BOARDS, "run_in_background": True}),
      result("b1", "PreToolUse:Bash hook error: refused", True), say(REPORT)], ALLOW, []),
]


def audit(transcripts: list[str]) -> int:
    """Replay real transcripts turn by turn and print every turn the signal would halt, so a false
    positive shows up against history before it shows up in a session. Only turns that mention a
    background acknowledgement are replayed."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cupcake_turn_scan as scan

    fired = turns = 0
    for f in transcripts:
        lines = Path(f).read_text(encoding="utf-8", errors="replace").splitlines()
        ends, start = [], 0
        for i, line in enumerate(lines):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and scan.is_real_user_prompt(ev):
                ends.append((start, i))
                start = i
        ends.append((start, len(lines)))
        for s, e in ends:
            chunk = "\n".join(lines[s:e])
            if "in background" not in chunk and "to the background" not in chunk:
                continue
            turns += 1
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "session.jsonl"
                path.write_text("\n".join(lines[:e]) + "\n", encoding="utf-8")
                out = subprocess.run(
                    ["bash", str(SIGNAL)],
                    input=json.dumps({"transcript_path": str(path), "hook_event_name": "Stop"}),
                    cwd=REPO_ROOT, text=True, capture_output=True, timeout=25,
                ).stdout.strip()
            if out:
                fired += 1
                print(f"{Path(f).name}:{e}: {out[:400]}")
    print(f"audit: {fired} of {turns} turns with a background launch would halt")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--audit":
        return audit(sys.argv[2:])
    failures = 0
    for name, events, want, needles in CASES:
        out = run_signal(events)
        got = HALT if out.startswith("UNTAILEDRUN:") else ALLOW if out == "" else f"odd:{out!r}"
        missing = [n for n in needles if n not in out]
        if got != want or missing:
            failures += 1
            print(f"FAIL {name}: want {want}, got {got}; missing {missing} ({out[:240]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    if failures:
        print(f"test-untailed-long-run-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-untailed-long-run-signal: OK ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
