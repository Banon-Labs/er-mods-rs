#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_markdown_rule`.

The signal emits `MDRULE|referent=<file or phrase>|sentence=<sentence>` when the turn closes on a
message that calls text in a markdown file a rule; empty otherwise. User correction 2026-11-11: "This
is probably the 100th time I've told you that its logically inconsistent to use the word rule in
reference to markdown files". The classifier is scripts/cupcake_markdown_rule.py.

Each case writes a crafted transcript, hands its path to the signal on stdin the way cupcake does, and
asserts the verdict and text the output must carry. The closers are verbatim from session 5ec7b779
where they name one.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_markdown_rule.sh"

NOV11 = (
    "The progress selftest and the similarity checks pass, and the rule is in "
    "`docs/agents/conventions.md` so later runs keep it."
)
CARRIES = "AGENTS.md now carries the same rule."
REPORT = "The similarity script now reads the release model from the v9 table."
REWRITE = "AGENTS.md describes it, and `.cupcake/policies/claude/no_unmeasured_estimate.rego` enforces it."


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
                                                     "content": [{"type": "text", "text": text}]}]}}


def run_signal(events: list[dict] | None) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        event = {"hook_event_name": "Stop"}
        if events is not None:
            path = Path(tmp) / "session.jsonl"
            path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
            event["transcript_path"] = str(path)
        proc = subprocess.run(
            ["bash", str(SIGNAL)], input=json.dumps(event),
            cwd=REPO_ROOT, text=True, capture_output=True, timeout=25,
        )
        return proc.stdout.strip()


HALT = "halt"
ALLOW = "allow"

# (name, events, verdict, substrings the output must carry)
CASES = [
    ("the 2026-11-11 20:21Z closer: the rule is in docs/agents/conventions.md",
     [user("Did you add timing to the counter lines?"), say(NOV11)], HALT,
     ("referent=`docs/agents/conventions.md`", "the rule is in")),
    ("the 2026-11-10 22:11Z closer: AGENTS.md now carries the same rule",
     [user("go"), say("I tested it end to end. " + CARRIES)], HALT, ("referent=AGENTS.md", CARRIES)),
    ("narration that puts the rule into AGENTS.md, as a closer",
     [user("go"), say("Next I'm writing the rule into AGENTS.md.")], HALT, ("referent=AGENTS.md",)),
    ("written rule", [user("go"), say("docs/agents/runtime-evidence.md carries the written rule.")],
     HALT, ("sentence=docs/agents/runtime-evidence.md carries the written rule.",)),
    ("a table row naming a markdown file and its rules",
     [user("go"), say("| File | Holds |\n|---|---|\n| `frida.md` | Frida setup and watchpoint rules |")],
     HALT, ("referent=`frida.md`", "watchpoint rules")),
    ("a pipe in the sentence does not split the fact line",
     [user("go"), say("The rule | the one for pushes | is in AGENTS.md.")], HALT, ("The rule / the one",)),
    ("the rewrite: AGENTS.md describes it, a policy enforces it", [user("go"), say(REWRITE)], ALLOW, ()),
    ("a rego rule and a game rule",
     [user("go"), say("The rego rule in `no_whole_check_sh.rego` refuses it, and AGENTS.md describes it. "
                      "The game's stacking rule is in docs/mechanics/buffs.md.")], ALLOW, ()),
    ("the word in quotes",
     [user("go"), say("The guard halts a closing message that calls markdown text a \"rule\".")], ALLOW, ()),
    ("a rule with no markdown referent", [user("go"), say("The new rule passes its selftest: 154 checks.")],
     ALLOW, ()),
    ("inside a code fence", [user("go"), say("```\nthe rule is in AGENTS.md\n```\n" + REPORT)], ALLOW, ()),
    ("in a mid-turn preamble, then a clean closer",
     [user("go"), say(CARRIES), use("t1", "Bash", {"command": "python3 scripts/x.py"}), result("t1", "ok"),
      say(REPORT)], ALLOW, ()),
    ("a turn that kept going after it",
     [user("go"), say(CARRIES), use("t1", "Bash", {"command": "python3 scripts/x.py"})], ALLOW, ()),
    ("in an earlier turn, a clean latest turn", [user("go"), say(CARRIES), user("and now?"), say(REPORT)],
     ALLOW, ()),
    ("no transcript in the hook event", None, ALLOW, ()),
]


def main() -> int:
    failures = 0
    for name, events, want, must in CASES:
        out = run_signal(events)
        got = HALT if out.startswith("MDRULE|") else ALLOW if out == "" else f"odd:{out!r}"
        problems = []
        if got != want:
            problems.append(f"want {want}, got {got}")
        problems += [f"output lacks {s!r}" for s in must if s not in out]
        if problems:
            failures += 1
            print(f"FAIL {name}: {'; '.join(problems)} ({out[:200]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    if failures:
        print(f"test-markdown-rule-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-markdown-rule-signal: OK ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
