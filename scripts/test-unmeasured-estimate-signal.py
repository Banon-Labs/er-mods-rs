#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `last_assistant_unmeasured_estimate`.

The signal emits `UNMEASURED|kind=<effort|runtime>|forecast=<sentence>` when the turn closes on a
message that forecasts time or effort with no measured number behind it; empty otherwise. User
correction 2026-11-11: "You also estimated yesterday over 24 hours ago after I started this project
that generating similiarity scores woudln't be a big lift", and "Should an LLM be prohibited from
making non-cited claims about timelines?". The reasoning is in docs/agents/runtime-evidence.md,
"Forecasts of time and effort are claims, cited or not made"; the classifier is
scripts/cupcake_unmeasured_estimate.py.

Each case writes a crafted transcript, hands its path to the signal on stdin the way cupcake does, and
asserts the verdict, the kind, and text the output must carry. The closers are verbatim from session
5ec7b779 where they name one.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "last_assistant_unmeasured_estimate.sh"

NOV09 = (
    "Yes, and it's nearly free. The kit already computes, for every spell and every rival skill, what "
    "it nets in each of the same 1,208 punish situations. That's a few seconds on top of a kit run. "
    "Adding melee-only ashes means scoring every skill once, roughly 20 to 30 minutes."
)
R10 = "r10 is running from sim-release (bdj5lkbhk, about 7 hours, tailed in a kitty tab)."
MEASURED = (
    "r10 has finished 38 of 944 sets, and its pace over the last 28 minutes was 17 sets, about 99 s "
    "per set. If r10's sets are spread the same way, its 38 sets are about half the stage, which "
    "leaves roughly 2 to 2.5 hours."
)
REPORT = "The similarity script now reads the release model from the v9 table."


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

# (name, events, verdict, kind, substrings the output must carry)
CASES = [
    ("the 2026-11-09 closer: nearly free, a few seconds, 20 to 30 minutes",
     [user("Is it reasonable to build similarity matrixies?"), say(NOV09)], HALT, "effort", ("nearly free",)),
    ("not a big lift", [user("go"), say("Generating similarity scores wouldn't be a big lift.")],
     HALT, "effort", ("big lift",)),
    ("a number on development work", [user("go"), say("Wiring the ashes in is about a couple of hours of work.")],
     HALT, "effort", ("hours of work",)),
    ("an effort word is not cleared by a measured number elsewhere",
     [user("go"), say(MEASURED + " Adding the melee pass should be quick.")], HALT, "effort", ("should be quick",)),
    ("a run's duration with no measurement: r10 about 7 hours", [user("go"), say(R10)],
     HALT, "runtime", ("about 7 hours",)),
    ("time left from a progress count with no elapsed time",
     [user("go"), say("Its full build r3 is at 16 of 1,278 attacks, about 30 minutes left.")],
     HALT, "runtime", ("30 minutes left",)),
    ("a pipe in the sentence does not split the fact line",
     [user("go"), say("The board regeneration | zerk will take ~7 h.")], HALT, "runtime", ("regeneration / zerk",)),
    ("a run-time forecast beside the pace it projects from", [user("go"), say(MEASURED)], ALLOW, None, ()),
    ("a run-time forecast beside a timed sample",
     [user("go"), say("A 20-entity sample took 412 s, so the 944 sets will take about 5.4 hours.")], ALLOW, None, ()),
    ("an old forecast reported in quotes beside the measured runs",
     [user("go"), say("I told you the matrix was \"nearly free\" and about 20 to 30 minutes. "
                      "The full runs took 6,863 s (r7), 9,827 s (r8) and 12,716 s (r9).")], ALLOW, None, ()),
    ("saying it is unmeasured and giving no number",
     [user("go"), say("I have not timed the melee pass, so I have no number for it.")], ALLOW, None, ()),
    ("a forecast inside a code fence", [user("go"), say("```\nETA 5 minutes\n```\n" + REPORT)], ALLOW, None, ()),
    ("a game duration", [user("go"), say("Twin Moons' roll cancel comes about 6.6 s after the press.")],
     ALLOW, None, ()),
    ("a forecast in a mid-turn preamble, then a clean closer",
     [user("go"), say(R10), *[use("t1", "Bash", {"command": "python3 scripts/x.py"}), result("t1", "ok")],
      say(REPORT)], ALLOW, None, ()),
    ("a turn that kept going after its forecast",
     [user("go"), say(R10), use("t1", "Bash", {"command": "python3 scripts/x.py"})], ALLOW, None, ()),
    ("a forecast in an earlier turn, a clean latest turn",
     [user("go"), say(R10), user("and now?"), say(REPORT)], ALLOW, None, ()),
    ("no transcript in the hook event", None, ALLOW, None, ()),
]


def main() -> int:
    failures = 0
    for name, events, want, kind, must in CASES:
        out = run_signal(events)
        got = HALT if out.startswith("UNMEASURED|") else ALLOW if out == "" else f"odd:{out!r}"
        problems = []
        if got != want:
            problems.append(f"want {want}, got {got}")
        if kind and f"|kind={kind}|" not in out:
            problems.append(f"output lacks kind={kind}")
        problems += [f"output lacks {s!r}" for s in must if s not in out]
        if problems:
            failures += 1
            print(f"FAIL {name}: {'; '.join(problems)} ({out[:200]!r})", file=sys.stderr)
        else:
            print(f"ok   [{want}] {name}")
    if failures:
        print(f"test-unmeasured-estimate-signal: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-unmeasured-estimate-signal: OK ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
