#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_unbacked_claim`.

The signal emits `UNBACKED:<sentence>` when the closing prose claims a repo artifact was built and
nothing backs it; empty otherwise. Each case writes a crafted transcript, hands its path to the
signal on stdin the way cupcake does, and asserts the tag.

The cases after the first two pin the 2026-10-19 widening: a path written by a Write/Edit/Bash call
in an earlier turn of the same session, or shown to exist by a read-only check in the judged turn,
backs a claim that names it. That turn was refused twice for naming
scripts/er-mechanics-set-modes-report.py, which a Write had created one turn earlier, after a task
notification opened a new turn and an `ls -l` in that turn had listed the file.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_unbacked_claim.sh"

SCRIPT = "/home/banon/projects/er-mods-rs/scripts/er-mechanics-set-modes-report.py"
CLOSER = (
    "Both files exist on disk. I wrote the script one turn earlier with the Write tool, and the "
    f"report it produced is beside it: {SCRIPT}"
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


def notify(task: str) -> dict:
    return user(
        f"<task-notification>\n<task-id>{task}</task-id>\n<status>completed</status>\n"
        "<summary>done</summary>\n</task-notification>"
    )


EARLIER_WRITE = [
    user("write the set-modes report script"),
    use("w1", "Write", {"file_path": SCRIPT, "content": "print('hi')\n"}),
    result("w1", f"File created successfully at: {SCRIPT}"),
    say("Started the report run in the background."),
]
LS_OK = [
    use("l1", "Bash", {"command": f"ls -l {SCRIPT}"}),
    result("l1", f"-rw-r--r-- 1 banon banon 4120 Oct 19 12:00 {SCRIPT}"),
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


CASES: list[tuple[str, list[dict], bool]] = [
    (
        "a claim with nothing written anywhere in the session still halts",
        [user("build the gate"), say("I built the conformance gate and wired the check into check.sh.")],
        True,
    ),
    (
        "a write in the judged turn still backs the claim, as before",
        [
            user("build the gate"),
            use("e1", "Edit", {"file_path": "scripts/check.sh", "old_string": "a", "new_string": "b"}),
            result("e1", "The file scripts/check.sh has been updated successfully."),
            say("I built the conformance gate and wired the check into check.sh."),
        ],
        False,
    ),
    (
        "the 2026-10-19 turn: Write one turn earlier, a task notification, ls -l, then the closer",
        EARLIER_WRITE + [notify("b1")] + LS_OK + [say(CLOSER)],
        False,
    ),
    (
        "a Write one turn earlier backs the claim with no check in the judged turn",
        EARLIER_WRITE + [notify("b1"), say(CLOSER)],
        False,
    ),
    (
        "an ls in the judged turn backs the claim with no earlier write",
        [user("is the script there?")] + LS_OK + [say(CLOSER)],
        False,
    ),
    (
        "a test -e that succeeded backs the claim",
        [
            user("is the script there?"),
            use("t1", "Bash", {"command": f"test -e {SCRIPT} && echo yes"}),
            result("t1", "yes"),
            say(CLOSER),
        ],
        False,
    ),
    (
        "an earlier Write whose result was an error wrote nothing",
        [
            user("write the script"),
            use("w1", "Write", {"file_path": SCRIPT, "content": "x"}),
            result("w1", "Error: permission denied", is_error=True),
            notify("b1"),
            say(CLOSER),
        ],
        True,
    ),
    (
        "an ls that reports the path missing does not back it",
        [
            user("is the script there?"),
            use("l1", "Bash", {"command": f"ls -l {SCRIPT}"}),
            result("l1", f"ls: cannot access '{SCRIPT}': No such file or directory", is_error=True),
            say(CLOSER),
        ],
        True,
    ),
    (
        "one named path backed and another never written still halts",
        EARLIER_WRITE + [
            notify("b1"),
            say(f"I wrote {SCRIPT} and scripts/er-mechanics-other-report.py earlier."),
        ],
        True,
    ),
    (
        "a claim that names no path anywhere still needs a write in the judged turn",
        EARLIER_WRITE + [notify("b1"), say("I wrote the script one turn earlier.")],
        True,
    ),
    (
        "a heredoc redirect in an earlier turn counts as a write of its target",
        [
            user("make the helper"),
            use("h1", "Bash", {"command": "cat > scripts/er-helper-x.sh <<'EOF'\necho hi\nEOF"}),
            result("h1", ""),
            notify("b1"),
            say("I wrote the helper script earlier, e.g. scripts/er-helper-x.sh is the one to run."),
        ],
        False,
    ),
    (
        "a successful Read in the judged turn backs the claim",
        [
            user("is the script there?"),
            use("r1", "Read", {"file_path": SCRIPT}),
            result("r1", "     1\tprint('hi')"),
            say(CLOSER),
        ],
        False,
    ),
    (
        "the 2026-10-20 turn: a deletion is not a claim that an artifact exists",
        [
            user("clean up the corpus-floor worktree"),
            use("d1", "Bash", {
                "command": "rm -rf /home/banon/projects/er-mods-rs/.worktrees/corpus-floor/target/fakehome",
            }),
            result("d1", ""),
            say("I deleted the agent's temporary 2.6 GB test home."),
        ],
        False,
    ),
    (
        "removal verbs stay silent with no tool call at all",
        [
            user("tidy up"),
            say("I removed the stale test script. I've cleared the old hook logs. I rm'd the gate's scratch dir."),
        ],
        False,
    ),
]


def main() -> int:
    failures = 0
    for name, events, expect_hit in CASES:
        out = run_signal(events)
        hit = out.startswith("UNBACKED:")
        if hit != expect_hit:
            failures += 1
            print(f"FAIL: {name}\n  expected {'a hit' if expect_hit else 'no hit'}, got {out!r}")
        else:
            print(f"ok:   {name}")
    if failures:
        print(f"{failures} of {len(CASES)} cases failed", file=sys.stderr)
        return 1
    print(f"all {len(CASES)} cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
