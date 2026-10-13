#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_inline_user_command`.

The signal emits `INLINEUSERCOMMAND:<detail>` when the closing prose carries a fenced code block
whose info string is a shell language, and nothing otherwise. User directive 2026-10-13: a command
the user is meant to run goes into its own kitty tab through scripts/kitty-user-command.py, never
into an inline bash block. Each case writes a crafted transcript, hands its path to the signal on
stdin the way cupcake does, and asserts the verdict -- and, for a halt, the text the policy quotes.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_inline_user_command.sh"

FENCE = "```"
PUSH = "git -C /home/banon/projects/er-mods-rs push -u origin fix/load-character-from-file"


def user(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


def say(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def use(uid: str, name: str, inp: dict) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]},
    }


def result(uid: str, text: str) -> dict:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": uid,
                                                     "content": text}]}}


def block(info: str, body: str = PUSH, fence: str = FENCE, indent: str = "") -> str:
    return f"{indent}{fence}{info}\n{indent}{body}\n{indent}{fence}"


def closer(*parts: str) -> list[dict]:
    return [user("publish the branch"), say("\n\n".join(parts))]


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
LEAD = "The branch is committed locally and the agent may not push it."

# (name, events, verdict, substrings the emitted detail must contain)
CASES: list[tuple[str, list[dict], str, tuple[str, ...]]] = [
    ("the 2026-10-13 shape: a bash block to run", closer(LEAD, block("bash")), HALT,
     ("fenced as bash", PUSH)),
    ("sh", closer(LEAD, block("sh")), HALT, ("fenced as sh",)),
    ("shell, upper case", closer(LEAD, block("SHELL")), HALT, ("fenced as shell",)),
    ("Bash, mixed case", closer(LEAD, block("Bash")), HALT, ("fenced as bash",)),
    ("zsh", closer(LEAD, block("zsh")), HALT, ("fenced as zsh",)),
    ("fish", closer(LEAD, block("fish")), HALT, ("fenced as fish",)),
    ("console", closer(LEAD, block("console", "$ " + PUSH)), HALT, ("fenced as console", "$ git")),
    ("shell-session", closer(LEAD, block("shell-session")), HALT, ("fenced as shell-session",)),
    ("space before the info string", closer(LEAD, block(" bash")), HALT, ("fenced as bash",)),
    ("attributes after the language", closer(LEAD, block('bash title="push"')), HALT,
     ("fenced as bash",)),
    ("pandoc braces", closer(LEAD, block("{.bash}")), HALT, ("fenced as bash",)),
    ("tilde fence", closer(LEAD, block("bash", fence="~~~")), HALT, ("fenced as bash",)),
    ("four-backtick fence", closer(LEAD, block("bash", fence="````")), HALT, ("fenced as bash",)),
    ("indented inside a list item",
     closer(LEAD, "1. Publish it:\n\n" + block("bash", indent="   ")), HALT, ("fenced as bash", PUSH)),
    ("inside a blockquote", closer(LEAD, block("bash", indent="> ")), HALT, ("fenced as bash", PUSH)),
    ("never closed (interrupted turn)", closer(LEAD, f"{FENCE}bash\n{PUSH}\n"), HALT, (PUSH,)),
    ("two blocks are counted, first one quoted",
     closer(LEAD, block("rust", "let x = 1;"), block("bash"), block("sh", "git status")), HALT,
     ("2 shell-fenced blocks", "fenced as bash", PUSH)),
    ("blank first line in the block",
     closer(LEAD, f"{FENCE}bash\n\n{PUSH}\n{FENCE}"), HALT, (PUSH,)),

    ("rust block", closer("Renamed the constant.", block("rust", "const LOAD_ROWS: usize = 3;")),
     ALLOW, ()),
    ("text block", closer("The log ends here.", block("text", "loaded slot 2")), ALLOW, ()),
    ("json block", closer("The fixture.", block("json", '{"a": 1}')), ALLOW, ()),
    ("toml block", closer("The profile row.", block("toml", 'path = "x.dll"')), ALLOW, ()),
    ("bare fence, no info string", closer("The output.", block("")), ALLOW, ()),
    ("inline git push", closer("The branch is committed; `git push` is yours to run."), ALLOW, ()),
    ("triple-backtick inline code on its own line",
     closer("The command:", f"{FENCE}git push{FENCE}"), ALLOW, ()),
    ("a bash fence mentioned mid-line", closer(f"The rule refuses a {FENCE}bash fence in a closer."),
     ALLOW, ()),
    ("a bash fence quoted inside a markdown block",
     closer("The rule refuses this shape:", "````markdown\n" + block("bash") + "\n````"), ALLOW, ()),
    ("bash after a rust block that closed", closer(block("rust", "let x = 1;"), block("bash")), HALT,
     ("fenced as bash",)),
    ("bash block mid-turn, followed by work, closer clean",
     [user("publish the branch"), say(LEAD + "\n\n" + block("bash")),
      use("k1", "Bash", {"command": "python3 scripts/kitty-user-command.py --title push --why x -- "
                                    + repr(PUSH)}),
      result("k1", "opened tab"), say("Opened a kitty tab titled push with the push command typed in.")],
     ALLOW, ()),
    ("bash block in an earlier turn, current closer clean",
     [*closer(LEAD, block("bash")), user("done"), say("The branch is on the remote now.")], ALLOW, ()),
    ("no text at all", [user("go")], ALLOW, ()),
]


def main() -> int:
    failures = 0
    for name, events, want, needles in CASES:
        out = run_signal(events)
        got = HALT if out.startswith("INLINEUSERCOMMAND:") else ALLOW if out == "" else f"odd:{out!r}"
        missing = [n for n in needles if n not in out]
        if got != want or missing:
            failures += 1
            extra = f", detail lacks {missing!r}" if missing else ""
            print(f"FAIL {name}: want {want}, got {got}{extra} ({out[:200]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    if failures:
        print(f"test-inline-user-command-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-inline-user-command-signal: OK ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
