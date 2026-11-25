#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_correction_encoded_first`.

The signal emits `CORRECTIONFACTS|...` when the user's latest typed prompt reads as a correction and
the turn it opened acted on the instance before encoding the correction for future agents, or ended
without encoding it and without saying why that is impossible; empty otherwise. Each case writes a
crafted transcript, hands its path to the signal on stdin the way cupcake does, and asserts the
verdict. `PROMPT_CASES` run the UserPromptSubmit signal `user_prompt_correction` the same way, on
the hook event's `prompt` field.

The first two cases are the turns the guard was written from (session 5ec7b779, 2026-11-11), with
their tool calls in the order the transcript recorded them; the commands are shortened, not changed
in kind.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_correction_encoded_first.sh"

_ids = iter(range(1, 10_000))


def typed(text: str, source: str = "typed") -> dict:
    return {"type": "user", "origin": {"kind": "human"}, "promptSource": source,
            "message": {"role": "user", "content": text}}


def untagged(text: str) -> dict:
    """A user event from before the harness recorded who typed it."""
    return {"type": "user", "message": {"role": "user", "content": text}}


def notification(task: str) -> dict:
    return {"type": "user", "origin": {"kind": "task-notification"}, "promptSource": "system",
            "message": {"role": "user", "content": f"<task-notification>\n<task-id>{task}</task-id>\n"
                                                    "<status>completed</status>\n</task-notification>"}}


def say(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def call(name: str, inp: dict, out: str = "ok", is_error: bool = False) -> list[dict]:
    uid = f"toolu_{next(_ids)}"
    res = {"type": "tool_result", "tool_use_id": uid, "content": out}
    if is_error:
        res["is_error"] = True
    return [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]}},
        {"type": "user", "message": {"content": [res]}},
    ]


def own_halt() -> dict:
    return {"type": "attachment", "attachment": {
        "type": "hook_blocking_error", "hookName": "Stop", "hookEvent": "Stop",
        "blockingError": {"blockingError": "Correction not encoded first: the user's message reads as a correction"}}}


def own_halt_meta() -> dict:
    return {"type": "user", "isMeta": True, "message": {"role": "user", "content":
            "Stop hook feedback:\nCorrection not encoded first: the user's message reads as a correction"}}


CORRECTION_1 = ("If I ever see a subagent that is finished but hasn't been closed, I always have VERY HIGH "
                "concerns that you consumed its results. I'm VERY WORRIED that you missed the changes UNTIL "
                "you close it")
CORRECTION_2 = "I was not concerned about you closing that specific agent whatsoever"

# Turn 2 of the measured failure, call for call.
MEASURED_TURN = [
    typed(CORRECTION_1),
    *call("ListAgents", {}, "This session is er-mods-rs-f2 ..."),
    *call("Bash", {"command": "cd /home/banon/projects/er-mods-rs && for d in . .worktrees/guards-main; do "
                              "echo \"== $d\"; python3 -c \"\nimport re,sys\nd=sys.argv[1]\n"
                              "t=open(d+'/scripts/cupcake_turn_scan.py').read()\nprint(t[:80])\n\" \"$d\"; done"},
          "== .\ndef blocked_on_user"),
    *call("Bash", {"command": "cd /home/banon/projects/er-mods-rs/.worktrees/guards-main && timeout 25 python3 "
                              "scripts/test-wall-of-text-classifier.py 2>&1 | tail -1; timeout 25 python3 "
                              "scripts/test-wall-of-text-signal.py 2>&1 | tail -1"},
          "wall-of-text classifier: all 27 cases passed"),
    *call("ToolSearch", {"query": "select:TaskStop", "max_results": 1}, ""),
    *call("TaskStop", {"task_id": "ac34f26adeea54127"},
          "<tool_use_error>No task found with ID: ac34f26adeea54127.</tool_use_error>", True),
    *call("SendMessage", {"to": "a4d14f2d4d71a5ba0", "summary": "Wrap up now and report final state",
                          "message": "Wrap up now."}, '{"success":true}'),
    say("The four subagents that finished are closed: the harness has no task left under any of their IDs."),
]

# What came between it and turn 3: the Stop, a task notification, and that turn's own work.
BETWEEN = [
    notification("a4d14f2d4d71a5ba0"),
    *call("TaskStop", {"task_id": "a4d14f2d4d71a5ba0"},
          "<tool_use_error>Task a4d14f2d4d71a5ba0 is not running (status: completed)</tool_use_error>", True),
    say("The release-model agent is closed."),
    *call("Agent", {"description": "Strip losing rules, merge dnok, test", "run_in_background": True,
                    "prompt": "Prepare the sim-release worktree for a full attack-similarity run (r10)."},
          "Async agent launched successfully."),
]

# Turn 3: the queued correction, then the Stop guard agent before anything else.
NEXT_TURN = [
    typed(CORRECTION_2, "queued"),
    *call("ListAgents", {}, "This session is er-mods-rs-f2 ..."),
    *call("Agent", {"description": "Stop guard: close finished subagents", "run_in_background": True,
                    "prompt": "Add a Cupcake Stop guard that halts a turn when a subagent has finished and the "
                              "main session has not closed it."},
          "Async agent launched successfully."),
    say("I'm turning that concern into a Stop guard."),
]

GUARD_EDIT = call("Edit", {"file_path": "/home/banon/projects/er-mods-rs/.cupcake/policies/claude/x.rego",
                           "old_string": "a", "new_string": "b"})
AGENTS_WRITE = call("Write", {"file_path": "/home/banon/projects/er-mods-rs/AGENTS.md", "content": "x"})
SOURCE_EDIT = call("Edit", {"file_path": "/home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py",
                            "old_string": "a", "new_string": "b"})
LAUNCH = call("Bash", {"command": "python3 scripts/er-run-branch.py --with er-net-effects"}, "launched")

# 2026-11-16, session 5ec7b779: the read this guard halted on, verbatim. `sed -n` prints lines, the
# inner `python3 -c` finds the line number, the outer one filters what sed printed; nothing writes.
SAVE_CACHE_READ = (
    "cd /home/banon/projects/er-mods-rs/.worktrees/r21 && sed -n \"$(python3 -c \"\n"
    "t=open('scripts/er-mechanics-attack-similarity.py').read().splitlines()\n"
    "print([i+1 for i,l in enumerate(t) if l.startswith('def save_cache(')][0])\"),+40p\" "
    "scripts/er-mechanics-attack-similarity.py | python3 -c \"import sys;[print(l.rstrip()[:150]) "
    "for l in sys.stdin if 'b{' in l or 'builds' in l or 'def ' in l]\""
)
NESTED_READ = call("Bash", {"command": SAVE_CACHE_READ}, "2203:def save_cache(builds, path):")
AGENTS_EDIT = call("Edit", {"file_path": "/home/banon/projects/er-mods-rs/AGENTS.md", "old_string": "a",
                            "new_string": "b"})
SED_IN_PLACE = call("Bash", {"command": "sed -i 's/a/b/' scripts/er-mechanics-attack-similarity.py"})
PY_OPEN_W = call("Bash", {"command": "python3 -c \"p='scripts/x.py'; open(p,'w').write('x')\""})
REDIRECT_INTO_REPO = call("Bash", {"command": "sed -n 1,40p scripts/a.py > scripts/b.py"})
TASK_STOP = call("TaskStop", {"task_id": "b9x"}, "stopped")
# 2026-11-17, session 5ec7b779: the guard halted on this help read, which only prints kitty's help for
# `get-text` and filters it; the AGENTS.md entry came right after it.
HELP_READ = call("Bash", {"command": "kitty @ get-text --help 2>&1 | grep -A4 -- '--extent' | head -8"},
                 "--extent\n  Default: screen")
KITTY_CLOSE = call("Bash", {"command": "kitty @ close-window --match id:382"})

HALT = "halt"
ALLOW = "allow"

CASES = [
    ("the measured turn: ListAgents, two read-only checks, TaskStop, SendMessage, prose",
     MEASURED_TURN, HALT, "verdict=instance_first|first_instance=TaskStop ac34f26adeea54127"),
    ("the next turn: a guard agent launched before anything else",
     [*MEASURED_TURN, *BETWEEN, *NEXT_TURN], ALLOW, None),
    ("a .cupcake edit first, then the instance fix",
     [typed(CORRECTION_1), *GUARD_EDIT, *SOURCE_EDIT, *LAUNCH, say("Guarded, then fixed.")], ALLOW, None),
    ("an AGENTS.md entry first, then the instance fix",
     [typed("I didn't say to stop"), *AGENTS_WRITE, *LAUNCH, say("Relaunched.")], ALLOW, None),
    ("a task prompt with instance edits is not judged",
     [typed("run r10"), *SOURCE_EDIT, *LAUNCH, say("r10 is running.")], ALLOW, None),
    ("a question about the game is not judged",
     [typed("what are the strictly worse ashes?"), say("Seven of them, listed below.")], ALLOW, None),
    ("a correction whose closing message argues impossibility",
     [typed("Didn't ask"),
      say("No guard can see this because the decision happened in the game's own menu, outside any "
          "transcript a signal reads.")], ALLOW, None),
    ("instance first, encoded after it",
     [typed(CORRECTION_1), *SOURCE_EDIT, *GUARD_EDIT, say("Fixed and guarded.")], HALT,
     "verdict=instance_first|first_instance=Edit /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py"
     "|encoded_later=1"),
    ("the same turn after this guard halted it once",
     [typed(CORRECTION_1), *SOURCE_EDIT, *GUARD_EDIT, say("Fixed and guarded."), own_halt(),
      say("The guard is in place.")], ALLOW, None),
    ("halted with no encoding, then encoded (Stop feedback recorded as a meta user event)",
     [typed(CORRECTION_1), *SOURCE_EDIT, say("Fixed."), own_halt_meta(), *GUARD_EDIT, say("Guarded.")],
     ALLOW, None),
    ("halted with no encoding, and still none",
     [typed(CORRECTION_1), *SOURCE_EDIT, say("Fixed."), own_halt(), say("It is fixed.")], HALT,
     "verdict=instance_first"),
    ("a correction answered with prose only",
     [typed("Why did you stash them?"), say("To keep the tree clean for the rebase.")], HALT, "verdict=unencoded"),
    ("a task notification does not start a new turn",
     [typed(CORRECTION_1), notification("bx1"), *call("TaskStop", {"task_id": "bx1"}, "stopped"),
      say("Stopped.")], HALT, "verdict=instance_first|first_instance=TaskStop bx1"),
    ("an agent briefed on guard counters is not enforcement",
     [typed(CORRECTION_1),
      *call("Agent", {"description": "rank guard counters", "prompt": "Rank guard counter damage per weapon."},
            "Async agent launched successfully."), say("Launched.")], HALT, "first_instance=Agent rank guard counters"),
    ("a prompt the harness did not mark as typed is never judged",
     [untagged(CORRECTION_1), *SOURCE_EDIT, say("Fixed.")], ALLOW, None),
    ("a later task prompt ends the correction's turn",
     [typed(CORRECTION_1), *SOURCE_EDIT, say("Fixed."), typed("run r10"), *LAUNCH, say("Running.")], ALLOW, None),
    ("2026-11-16: a nested sed/python read, then AGENTS.md, then the instance",
     [typed(CORRECTION_1), *NESTED_READ, *AGENTS_EDIT, *TASK_STOP, say("Encoded, then stopped.")], ALLOW, None),
    ("2026-11-17: a --help read piped into grep and head, then AGENTS.md, then the instance",
     [typed(CORRECTION_1), *HELP_READ, *AGENTS_EDIT, *TASK_STOP, say("Encoded, then stopped.")], ALLOW, None),
    ("a help read, then a real kitty mutation before AGENTS.md, acts on the instance",
     [typed(CORRECTION_1), *HELP_READ, *KITTY_CLOSE, *AGENTS_EDIT, say("Encoded.")], HALT,
     "verdict=instance_first|first_instance=Bash kitty @ close-window --match id:382"),
    ("sed -i before AGENTS.md acts on the instance",
     [typed(CORRECTION_1), *SED_IN_PLACE, *AGENTS_EDIT, say("Encoded.")], HALT,
     "verdict=instance_first|first_instance=Bash sed -i"),
    ("python open(p,'w') before AGENTS.md acts on the instance",
     [typed(CORRECTION_1), *PY_OPEN_W, *AGENTS_EDIT, say("Encoded.")], HALT, "verdict=instance_first"),
    ("a > redirect into a repo file before AGENTS.md acts on the instance",
     [typed(CORRECTION_1), *REDIRECT_INTO_REPO, *AGENTS_EDIT, say("Encoded.")], HALT,
     "first_instance=Bash sed -n 1,40p scripts/a.py > scripts/b.py"),
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


PROMPT_SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "user_prompt_correction.sh"

# The UserPromptSubmit half reads the hook event's `prompt` field, which carries harness-written text
# the transcript records apart from the user's words. 2026-11-22, session 5ec7b779: it quoted "do not
# repeat work that is already complete." from the usage-limit reset text.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import cupcake_correction_first as _cf  # noqa: E402

PROMPT_CASES = [
    ("the usage-limit reset text alone", _cf.USAGE_RESET, ""),
    ("the reset text with the /login record and a reminder, as the hook received it",
     _cf.USAGE_RESET_PROMPT, ""),
    ("the user's own words about repeated work", "stop redoing finished work, you repeated the run",
     "CORRECTION|stop redoing finished work"),
    ("the user's words typed after the harness text",
     _cf.USAGE_RESET_PROMPT + "\nDo not repeat work that is already complete, you redid the r10 run.",
     "CORRECTION|Do not repeat work"),
    ("a task request", "run r10", ""),
]


def run_prompt_signal(prompt: str) -> str:
    proc = subprocess.run(
        ["bash", str(PROMPT_SIGNAL)],
        input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt}),
        cwd=REPO_ROOT, text=True, capture_output=True, timeout=25,
    )
    return proc.stdout.strip()


def main() -> int:
    failures = 0
    for name, prompt, want in PROMPT_CASES:
        out = run_prompt_signal(prompt)
        bad = (out != "") if want == "" else not out.startswith(want)
        if bad:
            failures += 1
            print(f"FAIL prompt signal {name}: want {want!r}, got {out[:220]!r}", file=sys.stderr)
        else:
            print(f"ok   [prompt] {name}")
    for name, events, want, needle in CASES:
        out = run_signal(events)
        got = HALT if out.startswith("CORRECTIONFACTS|") else ALLOW if out == "" else f"odd:{out!r}"
        bad = got != want or (needle is not None and needle not in out)
        if bad:
            failures += 1
            print(f"FAIL {name}: want {want}{' with ' + repr(needle) if needle else ''}, got {got} ({out[:220]!r})",
                  file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    if failures:
        print(f"test-correction-first-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-correction-first-signal: OK ({len(CASES)} Stop cases, {len(PROMPT_CASES)} UserPromptSubmit cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
