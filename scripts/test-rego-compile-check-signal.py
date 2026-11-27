#!/usr/bin/env python3
"""Behavioral tests for the cupcake signal `rego_compile_check`.

The signal prints `REGOCHECK<TAB>error<TAB><path>` and opa's errors when a write would leave the
policy set the engine loads uncompiled, `REGOCHECK<TAB>unchecked...` when the check could not run, and
nothing otherwise. The incident (2026-11-11): an Edit made `.cupcake/policies/claude/
no_stop_waiting_agent.rego` call `value_of` before any edit defined it, cupcake could not compile,
and every tool call in every session was refused until the user deleted the file by hand.

Every case runs against a throwaway copy of this checkout's `.cupcake` (policies, system, signals,
rulebook), so no case writes to the live policy tree. The replay cases use the exact content of the
2026-11-11 write, kept in `REPLAY_BROKEN` below.

Skips (exit 0) when `opa` or `cupcake` is not on `PATH`, because the signal then answers `unchecked` by
design; the `unchecked` case runs regardless, with `PATH` emptied of both.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL = REPO_ROOT / ".cupcake" / "signals" / "rego_compile_check.sh"
POLICY_REL = "policies/claude/no_stop_waiting_agent.rego"

# The policy as it stood before the breaking Edit: compiles.
GOOD = '''# METADATA
# scope: package
# title: replay of no_stop_waiting_agent before the breaking edit
# custom:
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["TaskStop"]
#     required_signals: ["task_stop_waiting_agent"]
package cupcake.policies.claude.no_stop_waiting_agent

import rego.v1

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "TaskStop"
	field("id") != ""
	decision := {
		"rule_id": "ER-EFFECTS-NO-STOP-WAITING-AGENT",
		"reason": concat("", ["Agent ", field("id"), " (\\"", field("description"), "\\") is not finished."]),
		"severity": "HIGH",
	}
}

field(name) := value if {
	some part in split(input.signals.task_stop_waiting_agent, "|")
	startswith(part, concat("", [name, "="]))
	value := trim(substring(part, count(name) + 1, -1), " \\t\\r\\n")
}
'''
# The 2026-11-11 Edit, verbatim in effect: the reason calls `value_of`, which nothing defines yet.
BREAKING_EDIT = {"old_string": '" (\\"", field("description"), "\\") is not finished',
                 "new_string": '" (\\"", value_of("description"), "\\") is not finished'}
REPLAY_BROKEN = GOOD.replace(BREAKING_EDIT["old_string"], BREAKING_EDIT["new_string"])
# The same file with `value_of` defined: compiles.
FIXED = REPLAY_BROKEN + '''
value_of(name) := v if {
	v := field(name)
} else := ""
'''
# Cupcake 0.5.2 compiles a file under policies/ only when it carries a `METADATA` block (measured
# 2026-11-11: one without it is logged "Policy missing metadata" and left out of the set), so the
# error cases carry one and the no-metadata case shows the engine skipping the same error.
METADATA = ("# METADATA\n# scope: package\n# custom:\n#   routing:\n#     required_events: [\"PreToolUse\"]\n"
            "#     required_tools: [\"Bash\"]\n")
TYPE_ERROR_BODY = "package cupcake.policies.claude.typeerr\n\nimport rego.v1\n\nx := 1 + \"a\"\n"
TYPE_ERROR = METADATA + TYPE_ERROR_BODY
PARSE_ERROR = "package cupcake.system.parseerr\n\nimport rego.v1\n\nx if {\n\tinput.a ==\n"


def fresh_tree(tmp: Path) -> Path:
    """A copy of this checkout's `.cupcake` under `tmp`, returned as the `.cupcake` path."""
    root = tmp / "project" / ".cupcake"
    root.mkdir(parents=True)
    for name in ("policies", "system", "signals"):
        shutil.copytree(REPO_ROOT / ".cupcake" / name, root / name)
    shutil.copy2(REPO_ROOT / ".cupcake" / "rulebook.yml", root / "rulebook.yml")
    (root / "tests").mkdir()
    return root


def run_signal(event: dict, env: dict | None = None) -> str:
    proc = subprocess.run(["bash", str(SIGNAL)], input=json.dumps(event), text=True,
                          capture_output=True, timeout=25, env=env)
    return proc.stdout.strip()


def pre(tool: str, tool_input: dict, cwd: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": cwd}


ERROR, UNCHECKED, ALLOW = "error", "unchecked", "allow"


def verdict(out: str) -> str:
    if out.startswith("REGOCHECK\terror\t"):
        return ERROR
    if out.startswith("REGOCHECK\tunchecked\t"):
        return UNCHECKED
    return ALLOW if out == "" else f"odd:{out[:80]!r}"


def main() -> int:
    if not shutil.which("opa") or not shutil.which("cupcake"):
        print("test-rego-compile-check-signal: SKIP (opa or cupcake not on PATH)")
        return 0
    failures = 0
    total = 0
    with tempfile.TemporaryDirectory(prefix="rego-compile-check-test-") as tmp:
        cc = fresh_tree(Path(tmp))
        cwd = str(cc.parent)
        policy = cc / POLICY_REL
        other_dir = Path(tmp) / "elsewhere"
        other_dir.mkdir()
        (other_dir / "broken.rego").write_text(REPLAY_BROKEN, encoding="utf-8")

        def case(name: str, event: dict, want: str, must: tuple[str, ...] = (), env=None,
                 setup=None) -> None:
            nonlocal failures, total
            total += 1
            if setup:
                setup()
            out = run_signal(event, env)
            got = verdict(out)
            problems = [] if got == want else [f"want {want}, got {got}"]
            problems += [f"output lacks {s!r}" for s in must if s not in out]
            if problems:
                failures += 1
                print(f"FAIL {name}: {'; '.join(problems)} ({out[:300]!r})", file=sys.stderr)
            else:
                print(f"ok   [{want}] {name}")

        def write_good() -> None:
            policy.write_text(GOOD, encoding="utf-8")

        def drop_policy() -> None:
            policy.unlink(missing_ok=True)

        # The incident, both ways it could have arrived.
        case("replay: Write of the 2026-11-11 content (value_of undefined)",
             pre("Write", {"file_path": str(policy), "content": REPLAY_BROKEN}, cwd), ERROR,
             ("undefined function value_of", str(policy)), setup=drop_policy)
        case("replay: the Edit that called value_of before defining it",
             pre("Edit", {"file_path": str(policy), **BREAKING_EDIT}, cwd), ERROR,
             ("undefined function value_of",), setup=write_good)
        # Valid writes.
        case("Write of the fixed content (value_of defined)",
             pre("Write", {"file_path": str(policy), "content": FIXED}, cwd), ALLOW, setup=drop_policy)
        case("Edit that changes a message string",
             pre("Edit", {"file_path": str(policy), "old_string": "is not finished.",
                          "new_string": "is still waiting."}, cwd), ALLOW, setup=write_good)
        case("MultiEdit that calls value_of and defines it in the same call",
             pre("MultiEdit", {"file_path": str(policy), "edits": [
                 BREAKING_EDIT,
                 {"old_string": "field(name) := value if {",
                  "new_string": "value_of(name) := v if {\n\tv := field(name)\n} else := \"\"\n\n"
                                "field(name) := value if {"}]}, cwd), ALLOW, setup=write_good)
        # Other errors the engine reports.
        case("Write of a type error into policies/claude",
             pre("Write", {"file_path": str(cc / "policies/claude/typeerr.rego"), "content": TYPE_ERROR},
                 cwd), ERROR, ("rego_type_error",))
        case("Write of a parse error into system/",
             pre("Write", {"file_path": str(cc / "system/parseerr.rego"), "content": PARSE_ERROR}, cwd),
             ERROR, ("rego_parse_error",))
        # Files the engine does not load.
        case("a type error in a policy with no METADATA block (the engine leaves it out)",
             pre("Write", {"file_path": str(cc / "policies/claude/nometa.rego"),
                           "content": TYPE_ERROR_BODY.replace("typeerr", "nometa")}, cwd), ALLOW)
        case("broken content into tests/ (the engine does not load it)",
             pre("Write", {"file_path": str(cc / "tests/broken_test.rego"), "content": REPLAY_BROKEN},
                 cwd), ALLOW)
        case("broken content into a .rego outside any .cupcake",
             pre("Write", {"file_path": str(other_dir / "x.rego"), "content": REPLAY_BROKEN}, cwd), ALLOW)
        # Bash writes it can resolve.
        case("Bash heredoc of the broken content into policies/claude",
             pre("Bash", {"command": f"cat > {POLICY_REL.replace('policies', '.cupcake/policies')} "
                                     f"<<'EOF'\n{REPLAY_BROKEN}EOF\n"}, cwd), ERROR,
             ("undefined function value_of",), setup=drop_policy)
        case("Bash heredoc of the fixed content",
             pre("Bash", {"command": f"cat > .cupcake/{POLICY_REL} <<'EOF'\n{FIXED}EOF\n"}, cwd), ALLOW,
             setup=drop_policy)
        case("Bash cp of a broken file onto policies/claude",
             pre("Bash", {"command": f"cp -f {other_dir / 'broken.rego'} .cupcake/{POLICY_REL}"}, cwd),
             ERROR, ("undefined function value_of",), setup=drop_policy)
        case("Bash that only reads policies (opa test)",
             pre("Bash", {"command": f"opa test .cupcake/{POLICY_REL}"}, cwd), ALLOW)
        # Another file already broken: a write elsewhere is not blamed for it.
        broken_other = cc / "policies/claude/zz_already_broken.rego"

        def break_other() -> None:
            write_good()
            broken_other.write_text(TYPE_ERROR.replace("typeerr", "zz_already_broken"), encoding="utf-8")

        case("set already broken in another file; a valid Edit here is allowed",
             pre("Edit", {"file_path": str(policy), "old_string": "is not finished.",
                          "new_string": "is still waiting."}, cwd), ALLOW, setup=break_other)
        case("set already broken in another file; breaking this one is still refused",
             pre("Edit", {"file_path": str(policy), **BREAKING_EDIT}, cwd), ERROR,
             ("value_of",), setup=break_other)
        broken_other.unlink()
        # The check cannot run: it allows and says so.
        bare = {**os.environ, "PATH": "/usr/bin:/bin", "CUPCAKE_BIN": ""}
        case("no opa or cupcake on PATH: unchecked, not refused",
             pre("Write", {"file_path": str(policy), "content": REPLAY_BROKEN}, cwd), UNCHECKED,
             ("not on PATH",), env=bare, setup=drop_policy)
    if failures:
        print(f"test-rego-compile-check-signal: {failures} failure(s) of {total}", file=sys.stderr)
        return 1
    print(f"test-rego-compile-check-signal: OK ({total} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
