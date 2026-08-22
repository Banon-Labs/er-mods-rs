#!/usr/bin/env python3
"""End-to-end proof that the Stop-event guards actually halt a turn.

WHY THIS TEST EXISTS (2026-08-22). Every Stop guard in this repo was inert for 36 days -- from the
day the first one landed (2026-07-17) until the day this was written -- and the suite stayed green
the entire time, because the coverage was split in a way that left the real path untested:

  * scripts/test-*-signal.py           tested the SIGNAL shell scripts alone (no policy, no cupcake);
  * .cupcake/tests/*_test.rego         tested the POLICIES in the OPA INTERPRETER (`opa test`);
  * scripts/test-cupcake-policies.py   ran the real `cupcake eval` binary -- PreToolUse events ONLY.

`cupcake eval` does not use the OPA interpreter. It compiles the policies to WASM and runs them in
its own embedded runtime, where an unimplemented host builtin (`sprintf`) silently yields undefined
and the rule never fires. So the signal passed, the policy passed, the interpreter passed, and the
thing that actually runs at turn-end returned `{}` -- a clean ALLOW -- every single time.

This test closes that hole by driving the WHOLE path exactly as Claude Code does: the real
transcript on disk, the real signal scripts, the real `cupcake eval` command read out of
.claude/settings.json, and an assertion on the halt that comes back. Both directions are asserted --
a guard that cannot halt is useless, and a guard that halts on a clean turn wedges every session.

Fixtures live in .cupcake/tests/fixtures/*.jsonl and are ordinary Claude Code transcripts.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / ".cupcake" / "tests" / "fixtures"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"


@dataclass(frozen=True)
class Case:
    fixture: str
    # Distinctive substring of the expected halt reason, or None when the turn must be allowed.
    # Matched on the reason rather than the rule_id because cupcake renders a lone decision as a
    # bare reason string with no [rule_id] prefix.
    expect_halt_text: str | None
    why: str


CASES = [
    Case(
        "unexecuted_promise.jsonl",
        "promise nothing is going to keep",
        "turn ends on 'I'll re-run the gate...' with no tool call, no background work, no handoff",
    ),
    Case(
        "idle_hold.jsonl",
        "announced holding/idling",
        "turn is a pure pause announcing an idle hold while a background task runs",
    ),
    Case(
        "authority_agreement.jsonl",
        "authority-coded agreement",
        "turn opens with \"You're right\" -- banned agreement phrasing (2026-07-17 directive)",
    ),
    Case(
        "wall_of_text.jsonl",
        "paragraphs of prose",
        "turn is four paragraphs; the user reads one and skims the rest",
    ),
    Case(
        "stall_on_friction.jsonl",
        "handing the decision back",
        "user pushback met with an admission and a menu instead of the corrective action",
    ),
    Case(
        "clean.jsonl",
        None,
        "substantive work, no banned prose -- must NOT halt, or every turn wedges",
    ),
]


def stop_hook_command() -> list[str]:
    """The Stop hook command Claude Code actually runs, read from settings.json so this test
    follows the real configuration instead of a copy that can drift away from it."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    for group in settings.get("hooks", {}).get("Stop", []):
        for hook in group.get("hooks", []):
            cmd = hook.get("command", "")
            if "cupcake" in cmd:
                return cmd.replace("$CLAUDE_PROJECT_DIR", str(REPO_ROOT)).split()
    raise SystemExit("test-cupcake-stop-guards: no cupcake Stop hook found in .claude/settings.json")


def run_case(case: Case, argv: list[str]) -> str | None:
    """Returns None on pass, or a failure message."""
    fixture = FIXTURES / case.fixture
    if not fixture.is_file():
        return f"missing fixture {fixture}"

    with tempfile.TemporaryDirectory(prefix="cupcake-stop-guard-") as tmp:
        # Signals discover the transcript via ~/.claude/projects/<cwd-with-slashes-as-dashes>/*.jsonl
        # (scripts/cupcake_turn_scan.latest_transcript). Point HOME at a throwaway tree holding only
        # this fixture, so the test never reads the live session transcript.
        slug = str(REPO_ROOT).replace("/", "-")
        tdir = Path(tmp) / ".claude" / "projects" / slug
        tdir.mkdir(parents=True)
        shutil.copy(fixture, tdir / "session.jsonl")

        env = {**os.environ, "HOME": tmp, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)}
        event = json.dumps(
            {
                "session_id": f"stop-guard-{case.fixture}",
                "transcript_path": str(tdir / "session.jsonl"),
                "cwd": str(REPO_ROOT),
                "hook_event_name": "Stop",
                "stop_hook_active": False,
            }
        )
        proc = subprocess.run(
            argv, input=event, capture_output=True, text=True, env=env, timeout=25
        )

    raw = proc.stdout.strip()
    try:
        decision = json.loads(raw) if raw else {}
    except ValueError:
        return f"unparseable cupcake output: {raw[:200]!r}"

    reason = decision.get("reason", "")
    blocked = decision.get("decision") == "block"

    if case.expect_halt_text is None:
        if blocked:
            return f"expected NO halt (clean turn) but cupcake blocked: {reason[:160]!r}"
        return None

    if not blocked:
        return (
            f"expected a HALT but cupcake returned {raw or '{}'!r}.\n"
            f"      The guard is INERT -- this is the exact 2026-07-17..2026-08-22 defect. Check that\n"
            f"      no rule in its path uses a builtin Cupcake's WASM runtime cannot execute\n"
            f"      (run: python3 scripts/check-cupcake-wasm-builtins.py)."
        )
    if case.expect_halt_text not in reason:
        return f"halted, but reason lacked {case.expect_halt_text!r}: {reason[:200]!r}"
    return None


def main() -> int:
    if shutil.which("cupcake") is None:
        print("test-cupcake-stop-guards: SKIP (cupcake not installed)")
        return 0

    argv = stop_hook_command()
    failures = 0
    for case in CASES:
        err = run_case(case, argv)
        verdict = "halt" if case.expect_halt_text else "allow"
        if err:
            failures += 1
            print(f"FAIL [{verdict}] {case.fixture}: {case.why}\n      {err}", file=sys.stderr)
        else:
            print(f"ok   [{verdict}] {case.fixture}: {case.why}")

    if failures:
        print(f"\ntest-cupcake-stop-guards: {failures} failure(s)", file=sys.stderr)
        return 1
    print(f"test-cupcake-stop-guards: OK ({len(CASES)} cases through the real Stop hook)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
