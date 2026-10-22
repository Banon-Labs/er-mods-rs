#!/usr/bin/env python3
"""Behavioral tests for the `tool_miss_report` signal and `no_product_work_before_tool_fix.rego`.

User directive 2026-10-18: "I don't want you to fix problems before you fix tools". After the user
reports a guard miss, an Agent/Task dispatch that is not about the guards is refused for the rest
of that turn.

Transcript fixtures come from the `Chain` builder in `scripts/test-push-authorization-signal.py`,
so the human-prompt shape is the one the push and commit readers were measured against. The signal
gets a PreToolUse event on stdin the way cupcake hands it one; the last cases run `cupcake eval`
over a copy of the policy, which decides and executes nothing.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
READER = REPO_ROOT / "scripts" / "cupcake_tool_miss_report.py"
SHARED = REPO_ROOT / "scripts" / "cupcake_push_authorization.py"
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "tool_miss_report.sh"
POLICY = REPO_ROOT / ".cupcake" / "policies" / "claude" / "no_product_work_before_tool_fix.rego"

sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "push_signal_tests", REPO_ROOT / "scripts" / "test-push-authorization-signal.py")
push_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_tests)
Chain = push_tests.Chain

FAILURES: list[str] = []
REPORT = "Multiple rego violations uncaught in this response"
PRODUCT = "Fix the crit score so it counts only stance-break ripostes, in scripts/er-builds-optimize.py."
GUARD = "Work on the Cupcake stop-hook guards only (.cupcake/policies/claude/*.rego)."


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILURES.append(name)


def event_for(path: Path, tool_use_id: str, prompt: str, tool: str = "Agent", **extra) -> dict:
    ev = {"session_id": "fixture", "transcript_path": str(path), "cwd": str(REPO_ROOT),
          "hook_event_name": "PreToolUse", "tool_name": tool, "tool_use_id": tool_use_id,
          "tool_input": {"description": "work", "prompt": prompt}}
    ev.update(extra)
    return ev


def run_signal(event: dict) -> str:
    proc = subprocess.run(["bash", str(SIGNAL)], input=json.dumps(event), text=True,
                          capture_output=True, timeout=20)
    return proc.stdout.strip()


def chain_after(prompt: str, tool_use_id: str) -> Chain:
    c = Chain()
    c.human("score the claw builds")
    c.say("Done.")
    c.human(prompt)
    c.say("Dispatching.")
    c.use(tool_use_id, "")
    return c


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="tool-miss-test-"))
    try:
        p = chain_after(REPORT, "toolu_a").write(tmp / "a.jsonl")
        out = run_signal(event_for(p, "toolu_a", PRODUCT))
        check("a reported miss is read from the user's typed prompt",
              out.startswith("TOOLMISS|reported=1|prompt=Multiple rego"), out)

        p = chain_after("score the sword builds next", "toolu_b").write(tmp / "b.jsonl")
        out = run_signal(event_for(p, "toolu_b", PRODUCT))
        check("an ordinary prompt reads reported=0", out.startswith("TOOLMISS|reported=0|"), out)

        # The report sits in a task notification, not in anything the user typed.
        c = Chain()
        c.human("score the sword builds next")
        c.notification("the stop hook didn't fire on that message")
        c.use("toolu_c", "")
        p = c.write(tmp / "c.jsonl")
        out = run_signal(event_for(p, "toolu_c", PRODUCT))
        check("a notification is not the user's report", out.startswith("TOOLMISS|reported=0|"), out)

        p = chain_after(REPORT, "toolu_d").write(tmp / "d.jsonl")
        out = run_signal(event_for(p, "toolu_d", PRODUCT, agent_id="sub-1"))
        check("a subagent's dispatch is not judged", out == "", out)

        p = chain_after(REPORT, "toolu_e").write(tmp / "e.jsonl")
        out = run_signal({**event_for(p, "toolu_e", PRODUCT), "tool_name": "Bash",
                          "tool_input": {"command": "ls"}})
        check("a non-dispatch tool prints nothing", out == "", out)

        out = run_signal(event_for(tmp / "missing.jsonl", "toolu_f", PRODUCT))
        check("an unreadable transcript fails open", out == "", out)

        if shutil.which("cupcake") is None:
            print("SKIP end-to-end: cupcake not on PATH")
        else:
            root = tmp / "e2e"
            (root / "scripts").mkdir(parents=True)
            shutil.copytree(REPO_ROOT / ".cupcake" / "system", root / ".cupcake" / "system")
            (root / ".cupcake" / "signals").mkdir(parents=True)
            shutil.copy2(SIGNAL, root / ".cupcake" / "signals" / SIGNAL.name)
            (root / ".cupcake" / "policies" / "claude").mkdir(parents=True)
            shutil.copy2(POLICY, root / ".cupcake" / "policies" / "claude" / POLICY.name)
            (root / ".cupcake" / "rulebook.yml").write_text("signals: {}\nbuiltins: {}\n",
                                                            encoding="utf-8")
            shutil.copy2(READER, root / "scripts" / READER.name)
            shutil.copy2(SHARED, root / "scripts" / SHARED.name)

            def verdict(user_prompt: str, agent_prompt: str, tid: str) -> tuple[bool, str]:
                path = chain_after(user_prompt, tid).write(tmp / f"e2e-{tid}.jsonl")
                ev = event_for(path, tid, agent_prompt, permission_mode="default")
                proc = subprocess.run(
                    ["cupcake", "eval", "--harness", "claude", "--strict", "--log-level", "error"],
                    cwd=root, input=json.dumps(ev), text=True, capture_output=True, timeout=25)
                out = proc.stdout + proc.stderr
                return (proc.returncode != 0 or '"deny"' in out), out

            denied, out = verdict(REPORT, PRODUCT, "toolu_e1")
            check("cupcake refuses a product dispatch after a reported miss",
                  denied and "Fix the tool first" in out,
                  out[:400])
            denied, out = verdict(REPORT, GUARD, "toolu_e2")
            check("cupcake allows the guard dispatch after a reported miss", not denied, out[:400])
            denied, out = verdict("score the sword builds next", PRODUCT, "toolu_e3")
            check("cupcake allows a product dispatch with no report", not denied, out[:400])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if FAILURES:
        print(f"\ntest-tool-miss-report-signal: {len(FAILURES)} failure(s)", file=sys.stderr)
        return 1
    print("test-tool-miss-report-signal: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
