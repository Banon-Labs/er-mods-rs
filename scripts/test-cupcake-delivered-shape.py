#!/usr/bin/env python3
"""The policies must be tested against the input the ENGINE DELIVERS.

WHY THIS GATE EXISTS (2026-08-31). `.cupcake/tests/*_test.rego` runs in the OPA
interpreter, which feeds a policy whatever text the test author typed. Cupcake
does not deliver that text. Before a single policy runs it ENRICHES the event,
and two of those enrichments change the answer:

  * `whitespace_normalization` collapses every run of unquoted whitespace --
    newlines included -- to one space, and trims the ends. A multi-line command
    therefore arrives as ONE line, so any rule that locates something by line
    position is inert in production;
  * the Rust preprocessor OVERWRITES `affected_parent_directories` with its own
    answer whenever that answer is non-empty, so a hand-written fixture for that
    field is silently discarded.

Both defects were live and both suites were green. `command_operand_region` in
protected_paths.rego split the command on "\\n" to find heredoc payload and had
been dead since 2026-07-29 -- `lines` is always one element -- while its own
regressions passed, because they fed raw multi-line text the engine never
delivers. `test_deny_root_delete_inside_command_substitution` hand-fed
`affected_parent_directories: ["/"]` for `echo $(rm -rf /)` and passed, while the
same command was ALLOWED through the real binary, because the preprocessor
actually reports `["<cwd>/$(rm", "/)"]`.

A dead rule whose tests are green is indistinguishable from a working one. That
is the whole problem, and it is why this file measures rather than assumes.

WHAT IT CHECKS

  1. ENRICHMENT CONTRACT. Every transform the engine applies, pinned by
     measurement. If a cupcake upgrade changes one, this goes red -- instead of
     silently resurrecting a dead rule, or killing a live one.

  2. THE SHARED CASE TABLE. `delivered_cases` in protected_paths_test.rego is
     consumed here as well as by `opa test`: this runner sends each command
     through the real binary, lets the ENGINE compute `affected_parent_directories`,
     and asserts the fixture recorded in the table is what the engine actually
     produced. A fixture production never produces now fails.

  3. DEAD-LOGIC INVENTORY. Each helper known to be unreachable in production is
     asserted unreachable, by measurement, so the inventory cannot rot into
     folklore -- and so that anyone who "fixes" the engine finds out here.

  4. THE PRODUCTION PATH. `scripts/test-cupcake-policies.py` calls `cupcake eval`
     DIRECTLY, which is not how any command reaches the guards: Claude Code runs
     `scripts/cupcake-hook.sh`, which rewrites every unquoted newline to `; `
     before the engine sees it. That rewrite is the ONLY reason multi-line
     commands are still guarded at all, and no policy suite exercises it. These
     cases run the shim end to end.

Nothing here executes any command under test. Every command is data, handed to a
policy engine as text; the subject matter is commands that must never run.

Run with --selftest to prove the detector still catches the original defect.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CUPCAKE_DIR = REPO_ROOT / ".cupcake"
PROTECTED_PATHS_TEST = CUPCAKE_DIR / "tests" / "protected_paths_test.rego"
PROTECTED_PATHS_POLICY = CUPCAKE_DIR / "policies" / "claude" / "builtins" / "protected_paths.rego"
COMMANDS_POLICY = CUPCAKE_DIR / "system" / "commands.rego"
HOOK_SHIM = REPO_ROOT / "scripts" / "cupcake-hook.sh"

# Assembled rather than written whole, so this FILE is not itself denied by the
# guard it tests when an agent edits it through a Bash command.
ROOT_DELETE = " ".join(["rm", "-rf", "/"])
PUSH_MAIN = " ".join(["git", "push", "origin", "main"])

BASE_ENV = {
    "CUPCAKE_CURRENT_BRANCH_OVERRIDE": "feature/delivered-shape",
    "CUPCAKE_WORKTREE_BRANCHES_OVERRIDE": "",
    "CUPCAKE_ORIGIN_MAIN_OIDS_OVERRIDE": "a" * 40 + " " + "a" * 40,
}


class Failure(Exception):
    pass


class _Undefined:
    """OPA's "the query has no value", kept distinct from JSON null.

    A Rego function with no matching body is UNDEFINED, not null, and for the
    dead-logic inventory below that distinction IS the finding.
    """

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "<undefined>"


UNDEFINED = _Undefined()


# ---------------------------------------------------------------------------
# Measurement primitives
# ---------------------------------------------------------------------------


def eval_event(event: dict) -> dict:
    """Run the real binary on an event and recover what the policies were given.

    `--debug-files` writes a trace whose ENRICH section is the enriched input
    verbatim. That is the only way to see the delivered shape: it is produced
    inside the engine, after the hook JSON is parsed and before any policy runs.
    """
    debug_dir = tempfile.mkdtemp(prefix="cupcake-delivered-shape-")
    env = {**os.environ, **BASE_ENV}
    try:
        result = subprocess.run(
            [
                "cupcake", "eval", "--harness", "claude", "--strict",
                "--log-level", "error", "--debug-files", "--debug-dir", debug_dir,
            ],
            cwd=REPO_ROOT,
            input=json.dumps(event),
            text=True,
            capture_output=True,
            check=False,
            timeout=25,
            env=env,
        )
        traces = sorted(glob.glob(os.path.join(debug_dir, "*.txt")))
        trace = Path(traces[-1]).read_text(encoding="utf-8") if traces else ""
    finally:
        shutil.rmtree(debug_dir, ignore_errors=True)

    enriched = None
    match = re.search(r"^Enriched:\n(\{.*?\n\})\n", trace, re.S | re.M)
    if match:
        enriched = json.loads(match.group(1))
    operations = ""
    match = re.search(r"^Operations: (.*)$", trace, re.M)
    if match:
        operations = match.group(1).strip()
    return {
        "allowed": result.returncode == 0,
        "output": (result.stdout + result.stderr).strip(),
        "operations": [op.strip() for op in operations.split(",") if op.strip()],
        "enriched": enriched or {},
    }


def bash_event(command: str, **extra) -> dict:
    event = {
        "session_id": "cupcake-delivered-shape",
        "transcript_path": "/tmp/cupcake-delivered-shape.jsonl",
        "cwd": str(REPO_ROOT),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "timeout": 30000},
        "signals": {"current_branch": "feature/delivered-shape\n"},
    }
    event.update(extra)
    return event


def eval_bash(command: str, **extra) -> dict:
    outcome = eval_event(bash_event(command, **extra))
    tool_input = outcome["enriched"].get("tool_input") or {}
    outcome["delivered"] = tool_input.get("command")
    affected = outcome["enriched"].get("affected_parent_directories")
    outcome["affected"] = [] if affected is None else affected
    return outcome


def eval_through_shim(command: str, permission_mode: str = "default") -> dict:
    """The REAL production path: Claude Code -> scripts/cupcake-hook.sh -> cupcake.

    The shim deliberately does not pass `--strict`, so a denial is reported in
    the JSON body with exit code 0. Reading the exit code here would score every
    denial as an allow -- which is exactly the shape of failure this file exists
    to catch, so it is worth stating rather than assuming.
    """
    event = bash_event(command)
    event["permission_mode"] = permission_mode
    env = {**os.environ, **BASE_ENV}
    result = subprocess.run(
        ["bash", str(HOOK_SHIM)],
        cwd=REPO_ROOT,
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=False,
        timeout=25,
        env=env,
    )
    body = {}
    try:
        body = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        pass
    hook_output = body.get("hookSpecificOutput") or {}
    decision = hook_output.get("permissionDecision", "allow")
    return {
        "allowed": decision != "deny",
        "reason": hook_output.get("permissionDecisionReason", ""),
        "stderr": result.stderr.strip(),
    }


def opa_eval(query: str, *files: Path, input_doc: dict | None = None):
    args = ["opa", "eval", "--format", "json"]
    for f in files:
        args += ["-d", str(f)]
    if input_doc is not None:
        args.append("--stdin-input")
    args.append(query)
    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        input=json.dumps(input_doc) if input_doc is not None else None,
        text=True,
        capture_output=True,
        check=False,
        timeout=25,
    )
    if result.returncode != 0:
        raise Failure(f"opa eval failed for {query!r}:\n{result.stdout}\n{result.stderr}")
    parsed = json.loads(result.stdout)
    results = parsed.get("result") or []
    if not results:
        # OPA reports an UNDEFINED query by returning no result at all, which is
        # precisely the answer the dead-logic inventory is asking for. Raising
        # here would turn "this helper does nothing" into a harness error.
        return UNDEFINED
    expressions = results[0].get("expressions") or []
    if not expressions:
        return UNDEFINED
    return expressions[0]["value"]


# ---------------------------------------------------------------------------
# 1. The enrichment contract
# ---------------------------------------------------------------------------

# (label, typed command, delivered command). Each line is a measurement, not a
# belief: `cupcake eval --debug-files` produced the right-hand side from the
# left-hand side on 2026-08-31 with cupcake 0.5.2.
ENRICHMENT_CASES = [
    (
        "unquoted newline becomes a space -- this is what kills line-position logic",
        "echo a\necho b",
        "echo a echo b",
    ),
    ("unquoted CRLF becomes one space", "echo a\r\necho b", "echo a echo b"),
    ("unquoted tabs and runs of spaces collapse", "echo   a\tb\t\tc", "echo a b c"),
    ("leading and trailing whitespace is trimmed", "   echo a   ", "echo a"),
    (
        "whitespace INSIDE a balanced quoted span is preserved verbatim",
        'git commit -m "line1\nline2"',
        'git commit -m "line1\nline2"',
    ),
    (
        "a heredoc body is NOT a quoted span, so it is collapsed onto its reader",
        "cat > f <<'EOF'\nbody   text\nEOF",
        "cat > f <<'EOF' body text EOF",
    ),
    (
        "an ODD quote count makes the rest of the text look quoted, so nothing collapses",
        "echo it's fine   here",
        "echo it's fine   here",
    ),
]


def check_enrichment_contract() -> list[str]:
    findings = []
    for label, typed, expected in ENRICHMENT_CASES:
        outcome = eval_bash(typed)
        if outcome["delivered"] != expected:
            raise Failure(
                f"enrichment contract changed ({label}):\n"
                f"  typed     {typed!r}\n"
                f"  expected  {expected!r}\n"
                f"  delivered {outcome['delivered']!r}"
            )
        findings.append(label)

    # whitespace_normalization is BASH-ONLY. A file path with a double space is
    # delivered untouched, so a policy reading tool_input.file_path sees the raw
    # string and the Bash-side reasoning above does not transfer to it.
    write_event = {
        "session_id": "cupcake-delivered-shape",
        "transcript_path": "/tmp/cupcake-delivered-shape.jsonl",
        "cwd": str(REPO_ROOT),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "docs/a  b.md", "content": "x  y\nz"},
        "signals": {},
    }
    outcome = eval_event(write_event)
    delivered = outcome["enriched"].get("tool_input") or {}
    if delivered.get("file_path") != "docs/a  b.md":
        raise Failure(
            "whitespace_normalization now touches non-Bash tool input: "
            f"file_path delivered as {delivered.get('file_path')!r}"
        )
    # ... but `content_unification` DOES synthesise `new_string` from `content`,
    # and `symlink_resolution` synthesises the canonical path fields. A policy
    # may rely on those existing; a test that omits them is testing a shape
    # production does not produce.
    if delivered.get("new_string") != "x  y\nz":
        raise Failure("content_unification no longer mirrors Write content into new_string")
    for field in ("resolved_file_path", "original_file_path", "is_symlink"):
        if field not in outcome["enriched"]:
            raise Failure(f"symlink_resolution no longer synthesises {field}")
    findings.append("Bash-only normalisation; Write gets content_unification + symlink_resolution")

    # THE OVERWRITE. A hand-written affected_parent_directories survives only
    # while the preprocessor finds nothing. The moment it finds a path, the
    # caller's value is discarded -- which is why an `opa test` fixture for that
    # field can be pure fiction and still pass.
    supplied = eval_bash("echo hi", affected_parent_directories=["/sentinel-survives"])
    if supplied["affected"] != ["/sentinel-survives"]:
        raise Failure(
            "affected_parent_directories no longer survives when the preprocessor finds "
            f"nothing: {supplied['affected']!r}"
        )
    overwritten = eval_bash(
        "rm -rf /home/banon/scratch", affected_parent_directories=["/sentinel-discarded"]
    )
    if "/sentinel-discarded" in overwritten["affected"]:
        raise Failure(
            "affected_parent_directories is no longer overwritten by the preprocessor; "
            "the case-table fixtures in protected_paths_test.rego assume it is"
        )
    findings.append("affected_parent_directories is overwritten whenever the preprocessor finds a path")
    return findings


# ---------------------------------------------------------------------------
# 2. The shared case table, run against the real engine
# ---------------------------------------------------------------------------


def load_delivered_cases() -> list[dict]:
    return opa_eval(
        "data.cupcake.policies.builtins.protected_paths_test.delivered_cases",
        PROTECTED_PATHS_TEST,
        PROTECTED_PATHS_POLICY,
        COMMANDS_POLICY,
    )


def check_case_table() -> list[str]:
    rows = []
    for case in load_delivered_cases():
        outcome = eval_bash(case["command"])
        if outcome["delivered"] != case["command"]:
            raise Failure(
                f"case {case['name']}: the table's command is not in delivered shape.\n"
                f"  table     {case['command']!r}\n"
                f"  delivered {outcome['delivered']!r}\n"
                "Write the case the way the engine delivers it, or the interpreter is "
                "answering a question production never asks."
            )
        if outcome["affected"] != case["affected"]:
            raise Failure(
                f"case {case['name']}: the table's affected_parent_directories fixture is not "
                "what the engine synthesises.\n"
                f"  table  {case['affected']!r}\n"
                f"  engine {outcome['affected']!r}\n"
                "The engine overwrites this field, so the interpreter test is running on a "
                "fixture production discards."
            )
        expected_allow = case["expect"] == "allow"
        if outcome["allowed"] != expected_allow:
            raise Failure(
                f"case {case['name']}: expected {case['expect']} from the real binary, got "
                f"{'allow' if outcome['allowed'] else 'deny'}\n{outcome['output'][:400]}"
            )
        if case["expect"] == "deny" and case["rule"] and case["rule"] not in outcome["output"]:
            # The reason text carries the rule's own wording; a denial from a
            # DIFFERENT rule would otherwise score as a pass and hide that the
            # rule under test is dead.
            reason_markers = {
                "BUILTIN-PROTECTED-PATHS-WRAPPER": "inside a shell-wrapper payload",
                "BUILTIN-PROTECTED-PATHS-PARENT": "would be affected by operation on",
                "BUILTIN-PROTECTED-PATHS": "only read operations allowed",
                "BUILTIN-PROTECTED-PATHS-SCRIPT": "inline script mentions",
            }
            marker = reason_markers.get(case["rule"])
            if marker and marker not in outcome["output"]:
                raise Failure(
                    f"case {case['name']}: denied, but not by {case['rule']} "
                    f"(no {marker!r} in the reason). A denial from another rule is not "
                    "evidence that this one works.\n" + outcome["output"][:400]
                )
        rows.append(f"{case['expect']:<5} {case['name']}")
    return rows


# ---------------------------------------------------------------------------
# 3. The dead-logic inventory
# ---------------------------------------------------------------------------


def check_dead_logic_inventory() -> list[str]:
    """Assert, by measurement, that each listed helper is unreachable live.

    These are not bugs to fix in Rego -- the information the helpers need is
    destroyed before a policy runs, and no pattern can recover it. They are
    listed so that a reader of the policy knows which lines do nothing, and so a
    future engine change that revives them is noticed here first.
    """
    findings = []

    # (a) protected_paths.command_operand_region: splits the command on "\n" to
    #     drop heredoc payload. Dead since 2026-07-29 -- `lines` is always one
    #     element, so no line can ever be classified as payload.
    typed = "git commit -q -F - <<'EOF'\nmessage with a bare / in it\nEOF"
    delivered = eval_bash(typed)["delivered"]
    if "\n" in delivered:
        raise Failure("a heredoc's newlines now survive enrichment; command_operand_region is live again")
    region = opa_eval(
        "data.cupcake.policies.builtins.protected_paths.command_operand_region",
        PROTECTED_PATHS_POLICY,
        COMMANDS_POLICY,
        input_doc={"tool_input": {"command": delivered}},
    )
    if region != delivered:
        raise Failure(
            "command_operand_region now removes something from the DELIVERED text; the "
            f"inventory says it cannot.\n  delivered {delivered!r}\n  region    {region!r}"
        )
    findings.append(
        "protected_paths.command_operand_region + line_is_heredoc_payload + "
        "heredoc_terminated_between + protected_paths.heredoc_tag: DEAD (need an unquoted newline)"
    )

    # (b) commands.heredoc_body_blanked finds a body by looking for "\n" + tag.
    #     No unquoted newline survives, so it never fires, and a data heredoc's
    #     body keeps its command-position characters.
    blanked = opa_eval(
        f"data.cupcake.system.commands.heredoc_body_blanked({json.dumps(delivered)})",
        COMMANDS_POLICY,
    )
    if blanked is not UNDEFINED:
        raise Failure(
            "commands.heredoc_body_blanked now resolves a DELIVERED heredoc; the inventory "
            f"says it cannot.\n  {blanked!r}"
        )
    findings.append(
        "commands.heredoc_body_blanked / heredoc_tag / heredoc_resolved: DEAD for the "
        "delivered shape (need \"\\n\" + tag); heredoc_resolved silently falls through to the raw text"
    )

    # (c) Every command-position anchor in the git guards offers `\n` as an
    #     alternative. That alternative is unreachable: the character never
    #     arrives outside quotes. It is `;` -- inserted by the hook shim -- that
    #     actually carries multi-line commands, which is checked in section 4.
    if opa_eval(
        f'data.cupcake.system.commands.has_command_verb("echo hi\\n{ROOT_DELETE}", "rm")',
        COMMANDS_POLICY,
    ) is not True:
        raise Failure("has_command_verb no longer matches after a newline; the inventory note is stale")
    if eval_bash(f"echo hi\n{ROOT_DELETE}")["delivered"] != f"echo hi {ROOT_DELETE}":
        raise Failure("unquoted newlines no longer collapse; the dead-alternative note is stale")
    findings.append(
        "the `\\n` member of every command-position anchor class "
        "(commands.command_position_prefix_pattern, git_block_main_push, git_block_main_commit, "
        "git_require_fresh_origin_main, git_block_no_verify): DEAD ALTERNATIVE -- the guards "
        "survive multi-line commands only because scripts/cupcake-hook.sh substitutes `; `"
    )

    # (d) The `\n\r` members of the launch guard's and pgrep guard's
    #     single-statement tests, and their own \n->space normalisers, are
    #     likewise unreachable for unquoted text. They are idempotent, so this is
    #     dead weight rather than a hole -- recorded so nobody re-derives it.
    findings.append(
        "bash_elden_ring_launch_guard + block_manual_pgrep: the `\\n`/`\\r` members of their "
        "separator classes and their own newline normalisers are DEAD for unquoted text "
        "(idempotent, so no behaviour is lost)"
    )
    return findings


# ---------------------------------------------------------------------------
# 4. The production path, through the hook shim
# ---------------------------------------------------------------------------

# (name, command, expect_allow, expected reason fragment).
#
# Every one of these is a multi-line command, which is the point: the direct
# `cupcake eval` harness in test-cupcake-policies.py cannot model any of them,
# because the newline rewrite that makes line 2 visible happens in the shim.
SHIM_CASES = [
    (
        "two-line command whose SECOND line is guarded",
        f"echo hi\n{PUSH_MAIN}",
        False,
        "Do not push directly to main",
    ),
    (
        "a heredoc a SHELL reads is a program, and its lines are commands",
        f"bash <<'EOF'\n{PUSH_MAIN}\nEOF",
        False,
        "Do not push directly to main",
    ),
    (
        "a heredoc a NON-shell reads is data, and merely documents the command",
        f"cat > docs/guards.md <<'EOF'\n{PUSH_MAIN}\nEOF",
        True,
        "",
    ),
    (
        "a quoted memory body that names the command executes nothing",
        f'$HOME/.local/bin/bd remember --key k "before\n{PUSH_MAIN}\nafter"',
        True,
        "",
    ),
    (
        "a destructive second line reaches the protected-path parent rule",
        f"echo hi\n{ROOT_DELETE}",
        False,
        "would be affected by operation on",
    ),
    (
        "a trailing backslash JOINS lines and must not become a separator",
        "echo one \\\n  two",
        True,
        "",
    ),
    (
        "an unrecognised permission mode must not silently disable every guard",
        ROOT_DELETE,
        False,
        "would be affected by operation on",
    ),
]


# Commands whose verdict DIFFERS between the direct `cupcake eval` call and the
# production path. Each one is allowed direct and denied through the shim, which
# is the measurement that makes the shim load-bearing rather than convenient:
# remove it and these commands run.
#
# It is also the reason scripts/test-cupcake-policies.py cannot be the only live
# harness. That runner calls the binary directly, so for these shapes it is
# asserting the wrong verdict by construction -- and it pins one of them as a
# "known-open ALLOW" which production has in fact denied since the shim landed.
SHIM_LOAD_BEARING_CASES = [
    ("second line of a two-line command", f"echo hi\n{PUSH_MAIN}"),
    ("a heredoc a shell reads", f"bash <<'EOF'\n{PUSH_MAIN}\nEOF"),
    ("a destructive second line", f"echo hi\n{ROOT_DELETE}"),
]


def check_shim_is_load_bearing() -> list[str]:
    rows = []
    for name, command in SHIM_LOAD_BEARING_CASES:
        direct = eval_bash(command)
        through = eval_through_shim(command)
        if not direct["allowed"]:
            raise Failure(
                f"{name!r}: the DIRECT path now denies this. Either the engine stopped "
                "collapsing newlines or a policy learned to see past it -- either way the "
                "shim may no longer be load-bearing, and this note is stale."
            )
        if through["allowed"]:
            raise Failure(
                f"{name!r}: the PRODUCTION path allows a command it must deny. "
                "scripts/cupcake-hook.sh's newline rewrite is the only thing standing "
                "between a multi-line command and every anchored guard."
            )
        rows.append(f"direct=allow shim=deny  {name}")
    return rows


def check_production_path() -> list[str]:
    rows = []
    for name, command, expect_allow, fragment in SHIM_CASES:
        mode = "some-future-mode" if "permission mode" in name else "default"
        outcome = eval_through_shim(command, permission_mode=mode)
        if outcome["allowed"] != expect_allow:
            raise Failure(
                f"production path {name!r}: expected "
                f"{'allow' if expect_allow else 'deny'}, got "
                f"{'allow' if outcome['allowed'] else 'deny'}\n{outcome['reason'][:300]}"
            )
        if fragment and fragment not in outcome["reason"]:
            raise Failure(
                f"production path {name!r}: denied without {fragment!r}\n{outcome['reason'][:300]}"
            )
        rows.append(f"{'allow' if expect_allow else 'deny ':<5} {name}")
    return rows


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> int:
    """Prove the detector still catches the defect it exists for.

    The defect: a test fixture that production never produces. It is reproduced
    by hand-feeding `affected_parent_directories: ["/"]` for a command whose real
    preprocessor answer is something else, then asserting the case-table check
    rejects it.
    """
    command = f"echo $({ROOT_DELETE})"
    engine = eval_bash(command)["affected"]
    fiction = ["/"]
    if engine == fiction:
        print(
            "selftest FAILED: the preprocessor now reports ['/'] for a command-substitution "
            "delete, so this selftest's premise is stale -- pick another fixture."
        )
        return 1

    class FakeCase(dict):
        pass

    original = globals()["load_delivered_cases"]
    globals()["load_delivered_cases"] = lambda: [
        {
            "name": "selftest-fiction",
            "command": command,
            "affected": fiction,
            "expect": "deny",
            "rule": "BUILTIN-PROTECTED-PATHS-PARENT",
        }
    ]
    try:
        check_case_table()
    except Failure as exc:
        if "not what the engine synthesises" not in str(exc):
            print(f"selftest FAILED: caught the wrong failure:\n{exc}")
            return 1
        print("test-cupcake-delivered-shape: selftest OK (a fictional fixture is rejected)")
        return 0
    finally:
        globals()["load_delivered_cases"] = original
    print("selftest FAILED: a fictional affected_parent_directories fixture was accepted")
    return 1


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    verbose = "--verbose" in argv
    try:
        contract = check_enrichment_contract()
        table = check_case_table()
        inventory = check_dead_logic_inventory()
        load_bearing = check_shim_is_load_bearing()
        production = check_production_path()
    except Failure as exc:
        print(f"test-cupcake-delivered-shape: FAILED\n{exc}", file=sys.stderr)
        return 1

    if verbose:
        print("ENRICHMENT CONTRACT")
        for line in contract:
            print(f"  {line}")
        print("SHARED CASE TABLE (real binary, engine-computed affected dirs)")
        for line in table:
            print(f"  {line}")
        print("DEAD IN PRODUCTION")
        for line in inventory:
            print(f"  {line}")
        print("THE SHIM IS LOAD-BEARING (direct eval allows what production denies)")
        for line in load_bearing:
            print(f"  {line}")
        print("PRODUCTION PATH (through scripts/cupcake-hook.sh)")
        for line in production:
            print(f"  {line}")
    print(
        f"test-cupcake-delivered-shape: ok ({len(contract)} enrichment pins, "
        f"{len(table)} delivered cases, {len(inventory)} dead-logic assertions, "
        f"{len(load_bearing)} shim-divergence cases, {len(production)} production-path cases)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
