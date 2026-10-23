#!/usr/bin/env python3
"""Regression tests for repo-local Cupcake policy decisions."""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Per-eval cap, and it cannot rise: scripts/check-no-timeouts.py holds every subprocess timeout in
# this repo at or under 30s.
CASE_TIMEOUT_SECONDS = 30

# The compile every `cupcake eval` repeats. Each eval copies the rulebook into a fresh temp
# directory and runs `opa build -t wasm -O 2` on it, twice (project and global policies). Measured
# 2026-10-20 with `--trace signals,eval`: one eval was 1.18s wall and 2.36 CPU-seconds, and the
# project compile alone was 0.61s of the wall. This suite evaluates the same unchanged rulebook
# ~176 times, so it compiled the same two bundles ~176 times each; on a contended machine (the
# pre-push hook runs ten stages at once) that is what pushed single cases past the 30s cap. Routed
# through scripts/opa-build-cache.sh the compile happens once per bundle per run and an eval is
# 0.42s wall. The cache is keyed on the policy file contents, so it cannot hand back a stale
# bundle, and it lives in a temp directory that dies with this process.
OPA_BUILD_CACHE_SHIM = REPO_ROOT / "scripts" / "opa-build-cache.sh"
_REAL_OPA = shutil.which("opa")
_OPA_BUILD_CACHE_DIR = Path(tempfile.mkdtemp(prefix="cupcake-policy-regression-opa-cache-"))
atexit.register(shutil.rmtree, _OPA_BUILD_CACHE_DIR, True)


def cupcake_eval_argv(real_opa: str | None = _REAL_OPA) -> list[str]:
    """The eval command, through the compile cache when there is a real `opa` behind it.

    Without one, cupcake is left to report the missing binary itself rather than the shim
    failing in a way that reads as a policy verdict."""
    argv = ["cupcake", "eval", "--harness", "claude", "--strict", "--log-level", "error"]
    if real_opa and OPA_BUILD_CACHE_SHIM.is_file():
        argv[2:2] = ["--opa-path", str(OPA_BUILD_CACHE_SHIM)]
    return argv


class CaseTimedOut(Exception):
    """A case whose eval did not answer within CASE_TIMEOUT_SECONDS."""


@dataclass(frozen=True)
class PolicyCase:
    name: str
    command: str
    should_allow: bool
    expected_text: str | None = None
    extra_tool_input: dict[str, object] | None = None
    extra_event: dict[str, object] | None = None
    include_timeout: bool = True
    tool_name: str = "Bash"
    # Environment for the engine and every signal it runs. The runtime-evidence signals answer
    # from a live measurement of this checkout, so a case about a particular verdict pins it
    # through the overrides those signals provide, the way the branch guards use
    # `CUPCAKE_CURRENT_BRANCH_OVERRIDE`. Frozen dataclasses cannot hold a dict as a default, so
    # it arrives as pairs.
    extra_env: tuple[tuple[str, str], ...] = ()
    # Whether the fixture conversation ends on the user's `authorize commit`. `None` decides from
    # the command: one that names a commit or a lint gets the authorization, so a case written
    # about some other guard's reading of a commit message or a `shellcheck` call is still about
    # that guard. The cases about the commit and lint gates themselves say `False`.
    commit_authorization: bool | None = None
    # Text that must not appear in the output: a case denied by one guard can still assert that a
    # different guard stayed quiet, since cupcake lists every denial it collects.
    absent_text: str | None = None


DEFAULT_BASH_TIMEOUT_MS = 30000

# The commit and lint gates (user directive 2026-10-17) read the conversation, so a case that
# needs them open gets a real fixture transcript ending on the user's `authorize commit`, and the
# spends it causes are recorded here rather than in this checkout's `.cupcake/state`.
_COMMIT_AUTH_DIR = Path(tempfile.mkdtemp(prefix="cupcake-policy-regression-commit-auth-"))
atexit.register(shutil.rmtree, _COMMIT_AUTH_DIR, True)
COMMIT_AUTH_STATE = _COMMIT_AUTH_DIR / "commit-authorizations.jsonl"
COMMIT_AUTH_TRIGGERS = ("commit", "fmt", "clippy", "shellcheck", "ruff", "black", "prettier",
                        "eslint", "comment-caps", "lossy-utf8", "check.sh", "--fix")


def commit_auth_transcript(name: str, tool_name: str, tool_input: dict[str, object]) -> tuple[Path, str]:
    """A transcript whose last typed prompt is `authorize commit`, ending at this tool call."""
    tool_use_id = f"toolu_regression_{name}"
    prompt_uuid, call_uuid = f"{name}-prompt", f"{name}-call"
    entries = [
        {"type": "user", "uuid": prompt_uuid, "parentUuid": None, "isSidechain": False,
         "origin": {"kind": "human"}, "promptSource": "typed",
         "message": {"role": "user", "content": "authorize commit"}},
        {"type": "assistant", "uuid": call_uuid, "parentUuid": prompt_uuid, "isSidechain": False,
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": tool_use_id, "name": tool_name, "input": tool_input}]}},
    ]
    path = _COMMIT_AUTH_DIR / f"{name}.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path, tool_use_id

# Subprocess safety caps. Both were padding -- 60s and 120s guesses that had never been
# checked against how long the work takes -- and both now sit under the repo's 30s hard cap
# (scripts/check-no-timeouts.py MAX_TIMEOUT_SECONDS), with the measurement that sized them
# recorded so the next reader does not have to guess either.
#
# `opa test` over the four orphaned suites, measured 2026-08-31 on a box at loadavg ~9:
# 0.113s / 0.013s / 0.010s / 0.008s. Ten seconds is ~90x the slowest, and matches the value
# check-no-timeouts.py already uses for its own fast `git ls-files` call.
OPA_TEST_TIMEOUT_SECONDS = 10.0
# Why this file no longer shells out to test-cupcake-delivered-shape.py (2026-08-31).
# It used to, and its docstring said why: "adding a line to check.sh was out of scope" for the
# agent who wrote it. check.sh has carried those two lines at 421/422 since, so the gate ran
# twice per suite -- ~13.5s of the two runs (0.66s --selftest + 11.5-12.9s live, measured at
# loadavg ~9) duplicated for nothing. That duplication is also what pushed this script to 34.5s,
# past the 30s per-command cap, where a foreground call is SIGKILLed and reads as a hang.
# Removing it here leaves it a first-class enumerated step in check.sh, which times it, attributes
# its failure to it by name, and classifies a kill as inconclusive rather than burying it in an
# AssertionError raised by an unrelated runner.
#
# Coverage, checked rather than assumed: when this call was removed, neither
# `.github/workflows/check.yml` nor the pre-push hook ran check.sh, so simply deleting
# it would have silently dropped delivered-shape coverage from CI. Both were given the gate
# directly instead, in both its forms -- `--selftest` (proves it rejects a fictional fixture) and
# live (the real contract) are not the same run and neither substitutes for the other.
# check.yml has since been changed to run check.sh itself, and its hand-copied gate steps -- these
# two among them -- were removed with it, because a hand-copied subset is what let ~215 gates go
# unrun in CI in the first place. Both now run check.sh (2026-09-03), so its copy is
# still the only delivered-shape coverage there and must stay.

# Assembled rather than written whole, so this file is not itself denied by the
# guard it tests when an agent edits it through a Bash command.
ROOT_DELETE = " ".join(["rm", "-rf", "/"])

# `git worktree list --porcelain`-shaped fixture for the worktree-target
# exception cases in the main-commit/main-push guards.
WORKTREE_FIXTURE = (
    "worktree /home/banon/projects/er-mods-rs\n"
    "HEAD 0000000000000000000000000000000000000000\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /home/banon/projects/er-mods-rs/.worktrees/portrait-stats-crate\n"
    "HEAD 1111111111111111111111111111111111111111\n"
    "branch refs/heads/feature/portrait-stats-crate\n"
)

# The `frida_evidence` fixtures, for the same reason the four signal overrides above exist: a case
# whose verdict comes from a live measurement of this machine is not a test, it is a reading of
# whoever last ran a Frida session here.
#
# That is not hypothetical. `allow-teardown-that-is-the-whole-command` passed on a developer box
# and failed on a GitHub runner, because `teardown_must_relaunch` began consulting this signal and
# the two hosts disagree about it: `.cupcake/signals/frida_evidence.sh` runs
# `scripts/er-frida-evidence.py --check`, which reads a log under `XDG_STATE_HOME`, and a runner
# has never written one. Reproduced with `XDG_STATE_HOME` pointed at an empty directory.
#
# Pinned through `ER_FRIDA_EVIDENCE_LOG`, the reader's own log-path override, rather than by
# teaching the signal a `CUPCAKE_..._OVERRIDE` word. That script documents itself as the one signal
# here that fails closed, because "a broken evidence reader must not quietly hand out permission to
# edit" -- and an env var that makes it print `PROVEN` is exactly that hole. Pointing it at a log
# keeps the verdict coming from the real reader, so these cases exercise it end to end instead of
# stubbing it out.
_FRIDA_FIXTURE_DIR = Path(tempfile.mkdtemp(prefix="cupcake-policy-regression-frida-"))
atexit.register(shutil.rmtree, _FRIDA_FIXTURE_DIR, True)

# Never created. `newest_record` takes the `OSError` as "no log", so the reader prints
# `UNPROVEN no-frida-evidence` on every host, which is the fail-closed verdict every case gets
# unless it asks for another one.
FRIDA_EVIDENCE_ABSENT = _FRIDA_FIXTURE_DIR / "absent.jsonl"

# A telemetry row dated far enough ahead that `head_commit_time` can never spend it. Without that
# the verdict would depend on when this checkout last committed a Rust change, which is the same
# host-dependence in a different coat.
FRIDA_EVIDENCE_PROVEN = _FRIDA_FIXTURE_DIR / "proven.jsonl"
FRIDA_EVIDENCE_PROVEN.write_text(
    json.dumps(
        {
            "at": 2**40,
            "kind": "telemetry",
            "crate": "er-policy-regression",
            "log": "/dev/null",
            "line": "[policy-regression] a measurement that is not this machine's",
        }
    )
    + "\n",
    encoding="utf-8",
)



def _make_conflict_fixture(root: Path) -> tuple[Path, Path]:
    """A repo stopped in a merge with one crate source conflicted and one clean.

    Returns `(conflicted, clean)`, both absolute. The Frida gate admits an Edit to the first --
    resolving it combines changes that are each already committed -- and must still refuse the
    second, which sits in the same stopped merge but has nothing to resolve. Built for real rather
    than described, because the verdict comes from git (`ls-files --unmerged` and the git dir's
    `MERGE_HEAD`), not from anything in the event.
    """
    repo = root / "conflict-repo"
    conflicted = repo / "crates" / "demo" / "src" / "addr.rs"
    clean = repo / "crates" / "demo" / "src" / "lib.rs"
    conflicted.parent.mkdir(parents=True)

    def git(*argv: str, check: bool = True) -> None:
        subprocess.run(
            [
                "git",
                "-c", "user.name=policy-regression",
                "-c", "user.email=policy-regression@invalid",
                "-c", "commit.gpgsign=false",
                "-c", "core.hooksPath=/dev/null",
                "-c", "init.defaultBranch=base",
                "-C", str(repo),
                *argv,
            ],
            capture_output=True,
            check=check,
            timeout=10,
        )

    git("init", "-q")
    conflicted.write_text("pub const A: u32 = 0;\n", encoding="utf-8")
    clean.write_text("mod addr;\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "side")
    conflicted.write_text("pub const A: u32 = 1;\n", encoding="utf-8")
    git("commit", "-q", "-am", "side")
    git("checkout", "-q", "base")
    conflicted.write_text("pub const A: u32 = 2;\n", encoding="utf-8")
    git("commit", "-q", "-am", "base change")
    git("merge", "-q", "side", check=False)
    return conflicted, clean


# Under the gitignored `target/` rather than `/tmp`, because a `.rs` path in `/tmp` is refused by
# the scratch-script guard before the Frida gate is ever asked.
(REPO_ROOT / "target").mkdir(exist_ok=True)
_CONFLICT_FIXTURE_DIR = Path(
    tempfile.mkdtemp(prefix="cupcake-policy-regression-conflict-", dir=REPO_ROOT / "target")
)
atexit.register(shutil.rmtree, _CONFLICT_FIXTURE_DIR, True)
CONFLICTED_RUST, CLEAN_RUST_IN_CONFLICTED_REPO = _make_conflict_fixture(_CONFLICT_FIXTURE_DIR)

# The read-only listing denied on the native-Linux dev box on 2026-09-26, verbatim: a check for
# running cargo/rustc builds and agent sessions before a disk cleanup deleted Rust target/ dirs.
# It tripped two guards, neither of whose premise held -- the WSL pgrep false-negative, and a
# build grepped for errors when `cargo|rustc` was only pgrep's pattern.
REPORTED_PROCESS_LISTING = (
    "pgrep -a -f 'cargo|rustc' | grep -v pgrep | cut -c1-200 | head; "
    'echo "--- agents:"; pgrep -a -x claude | cut -c1-120; '
    "pgrep -a -f '(^| |/)pi( |$)' | cut -c1-120 | head; "
    'echo "--- scan:"; cat /tmp/.../tasks/bv7bzk2t8.output'
)


def run_case(case: PolicyCase) -> None:
    tool_input: dict[str, object] = {"command": case.command}
    if case.include_timeout:
        tool_input["timeout"] = DEFAULT_BASH_TIMEOUT_MS
    if case.extra_tool_input:
        tool_input.update(case.extra_tool_input)
    event = {
        "session_id": f"cupcake-policy-regression-{case.name}",
        "transcript_path": f"/tmp/cupcake-policy-regression-{case.name}.jsonl",
        "cwd": str(REPO_ROOT),
        "hook_event_name": "PreToolUse",
        "tool_name": case.tool_name,
        "tool_input": tool_input,
        "signals": {"current_branch": "feature/policy-regression\n"},
    }
    authorize = case.commit_authorization
    if authorize is None:
        authorize = any(word in case.command for word in COMMIT_AUTH_TRIGGERS)
    if authorize:
        path, tool_use_id = commit_auth_transcript(case.name, case.tool_name, tool_input)
        event["transcript_path"] = str(path)
        event["tool_use_id"] = tool_use_id
    if case.extra_event:
        event.update(case.extra_event)
    env = {**os.environ}
    env["CUPCAKE_COMMIT_AUTHORIZATION_STATE"] = str(COMMIT_AUTH_STATE)
    # CI checks out detached commits, making `git branch --show-current` empty.
    # Policy-regression allow-cases should model a normal feature branch by default, but
    # cases that explicitly set or remove the current_branch signal must control the
    # override too; otherwise live Cupcake eval cannot exercise main/missing-branch guards.
    signals = event.get("signals")
    if isinstance(signals, dict) and "current_branch" in signals:
        branch_signal = signals["current_branch"]
        if isinstance(branch_signal, dict):
            env["CUPCAKE_CURRENT_BRANCH_OVERRIDE"] = str(branch_signal.get("output", ""))
        else:
            env["CUPCAKE_CURRENT_BRANCH_OVERRIDE"] = str(branch_signal)
    elif isinstance(signals, dict):
        env["CUPCAKE_CURRENT_BRANCH_OVERRIDE"] = ""
    else:
        env["CUPCAKE_CURRENT_BRANCH_OVERRIDE"] = "feature/policy-regression"
    # Same live-signal control for the worktree_branches signal (worktree-target
    # exception in the main-commit/main-push guards): cases that model worktree
    # state must pin it, everything else runs with an empty worktree list so the
    # exception can never fire by accident.
    if isinstance(signals, dict) and "worktree_branches" in signals:
        wt_signal = signals["worktree_branches"]
        if isinstance(wt_signal, dict):
            env["CUPCAKE_WORKTREE_BRANCHES_OVERRIDE"] = str(wt_signal.get("output", ""))
        else:
            env["CUPCAKE_WORKTREE_BRANCHES_OVERRIDE"] = str(wt_signal)
    else:
        env["CUPCAKE_WORKTREE_BRANCHES_OVERRIDE"] = ""

    if isinstance(signals, dict) and "origin_main_oids" in signals:
        oid_signal = signals["origin_main_oids"]
        env["CUPCAKE_ORIGIN_MAIN_OIDS_OVERRIDE"] = str(oid_signal.get("output", "") if isinstance(oid_signal, dict) else oid_signal)
    else:
        env["CUPCAKE_ORIGIN_MAIN_OIDS_OVERRIDE"] = "a" * 40 + " " + "a" * 40

    # Same defensive default for the runtime-evidence verdict, and for the same reason the two
    # above have one: the cases that are about this guard pin it through `extra_env` below, and
    # every other push-shaped case must not inherit a live measurement of this machine.
    #
    # Without this line the verdict came from `.cupcake/signals/runtime_evidence_for_head.sh`
    # reading the real checkout, so `allow-git-push-feature-branch` -- a case about whether an
    # explicit feature refspec escapes the `main`-push guard -- went red on any branch that edits
    # `crates/` and has not been launched. That is every crate-touching branch before its first
    # run, which is when this suite is most likely to be run. The file's own header already states
    # the rule this restores: a pinned verdict says "nothing about what has been built or launched
    # on this machine".
    #
    # `NOTRUNTIME` rather than `OK`: it is the verdict for a push that touches no crate, which is
    # what these fixtures are -- synthetic command strings that push nothing. It can never deny,
    # so it cannot mask a different guard's refusal, and it does not assert a run happened.
    env["CUPCAKE_RUNTIME_EVIDENCE_OVERRIDE"] = "NOTRUNTIME"

    # And the fifth host-dependent verdict, pinned for the reason the four above are. This one
    # arrived late: `CUPCAKE_RUNTIME_EVIDENCE_OVERRIDE` does not reach the guards that read
    # `frida_evidence`, so until this line they read whatever Frida history this machine happened
    # to hold, and a case about `teardown_must_relaunch` went green here and red on a runner.
    # Absent by default is the fail-closed direction: it can only ever withhold the exception, so
    # it cannot mask a guard that should have denied. A case that wants the other verdict names a
    # log through `extra_env`, which is applied below and wins.
    env["ER_FRIDA_EVIDENCE_LOG"] = str(FRIDA_EVIDENCE_ABSENT)

    # The host_platform signal behind `block_manual_pgrep` answers from the machine running this
    # suite, and the pgrep guard stands down on native Linux. Every pgrep deny case below is about
    # the WSL guard, and CI and the dev box are both native Linux, so without this pin those cases
    # would go red for a reason that has nothing to do with the policy. `wsl` by default; the cases
    # about native hosts pin `native` through `extra_env`.
    env["CUPCAKE_HOST_PLATFORM_OVERRIDE"] = "wsl"

    env.update(dict(case.extra_env))
    env["ER_OPA_BUILD_CACHE"] = str(_OPA_BUILD_CACHE_DIR)
    if _REAL_OPA:
        env["ER_OPA_REAL"] = _REAL_OPA

    try:
        result = subprocess.run(
            cupcake_eval_argv(),
            cwd=REPO_ROOT,
            input=json.dumps(event),
            text=True,
            capture_output=True,
            check=False,
            timeout=CASE_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaseTimedOut(case.name) from exc
    output = result.stdout + result.stderr
    allowed = result.returncode == 0
    if allowed != case.should_allow:
        raise AssertionError(
            f"{case.name}: expected allow={case.should_allow}, got returncode={result.returncode}\n{output}"
        )
    if case.expected_text and case.expected_text not in output:
        raise AssertionError(f"{case.name}: missing {case.expected_text!r}\n{output}")
    if case.absent_text and case.absent_text in output:
        raise AssertionError(f"{case.name}: unexpected {case.absent_text!r}\n{output}")


# Rego unit suites that had a test file and no runner. scripts/check.sh
# enumerates its `opa test` invocations one line at a time, and these four were
# never on the list: 86 assertions written, committed, and never executed once.
# check.sh already carries a comment about this exact failure happening to two
# other suites; running them from here is what stops it being three times.
#
# The protected-paths suite is the one that matters most, because
# BUILTIN-protected-paths-parent and -wrapper are the rules standing between an
# agent and a root delete, and until today nothing ran their tests at all.
# It is also where a new suite belongs. check.sh lists its `opa test` invocations
# one line at a time, so a suite added to .cupcake/tests/ without an edit to that
# list is born orphaned -- which is how four of them accumulated 89 never-executed
# assertions. `opa test .cupcake/` would run everything and no gate calls it.
ORPHANED_REGO_SUITES = [
    [
        ".cupcake/system/commands.rego",
        ".cupcake/policies/claude/builtins/protected_paths.rego",
        ".cupcake/tests/protected_paths_test.rego",
    ],
    [
        ".cupcake/system/commands.rego",
        ".cupcake/policies/claude/guard_layer_destructive_guard.rego",
        ".cupcake/tests/guard_layer_destructive_guard_test.rego",
    ],
    [
        ".cupcake/policies/claude/edit_no_tmp_scripts_guard.rego",
        ".cupcake/tests/edit_no_tmp_scripts_guard_test.rego",
    ],
    [
        ".cupcake/policies/claude/monitor_rate_limit.rego",
        ".cupcake/tests/monitor_rate_limit_test.rego",
    ],
    [
        # `commands.rego` is listed because the policy reads the shared command-slot
        # decomposition from it. An orphaned suite compiles only the files named here, so a
        # missing dependency is nine `undefined function` errors rather than a failing test.
        ".cupcake/system/commands.rego",
        ".cupcake/policies/claude/teardown_must_relaunch.rego",
        ".cupcake/tests/teardown_must_relaunch_test.rego",
    ],
    [
        ".cupcake/policies/claude/no_source_edit_during_live_run.rego",
        ".cupcake/tests/no_source_edit_during_live_run_test.rego",
    ],
    [
        ".cupcake/policies/claude/no_unbacked_claim.rego",
        ".cupcake/tests/no_unbacked_claim_test.rego",
    ],
    [
        ".cupcake/policies/claude/no_repo_network_banners_prompt_context.rego",
        ".cupcake/tests/no_repo_network_banners_prompt_context_test.rego",
    ],
]


# The two signals behind `ER-EFFECTS-REQUIRE-RUNTIME-EVIDENCE`, checked as signals rather than
# through a verdict, because both ways they can go wrong are invisible from a policy test.
#
# A signal that exits non-zero does not reach the policy as its output at all: cupcake replaces the
# string with `{"error": ..., "exit_code": 1, "output": ..., "success": false}`, and every
# comparison the policy makes against a word is then undefined. The note signal did exactly this
# for its whole life -- `er-runtime-evidence.py` exits 1 when no log names the tip, `pipefail`
# carried that out as the script's status, and that is the one verdict that reads the note -- so a
# live refusal on 2026-09-13 said "no measurement was available" while the measurement sat inside
# the discarded object.
#
# And the verdict has to be about the repository the command pushes. The same day, a session in
# the main checkout ran `cd <another worktree> && git push` and was refused over the main
# checkout's tip, a commit the push did not contain; the same mix-up passes a push of unproven
# game code whenever the session's own directory happens to have evidence. The cases below use a
# target that cannot exist, so they assert the wiring without depending on what has been built or
# launched on this machine.
RUNTIME_EVIDENCE_SIGNALS = (
    ".cupcake/signals/runtime_evidence_for_head.sh",
    ".cupcake/signals/runtime_evidence_note.sh",
)

SIGNAL_TIMEOUT_SECONDS = 25.0


def _signal_event(command: str) -> str:
    return json.dumps(
        {
            "session_id": "cupcake-signal-contract",
            "transcript_path": "/tmp/cupcake-signal-contract.jsonl",
            "cwd": str(REPO_ROOT),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command, "timeout": DEFAULT_BASH_TIMEOUT_MS},
        }
    )


def _run_signal(script: str, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(REPO_ROOT / script)],
        cwd=REPO_ROOT,
        input=_signal_event(command),
        text=True,
        capture_output=True,
        check=False,
        timeout=SIGNAL_TIMEOUT_SECONDS,
    )


def run_runtime_evidence_signal_checks() -> None:
    absent = "/nonexistent-checkout-cupcake-signal-contract"
    for script in RUNTIME_EVIDENCE_SIGNALS:
        for command in ("cargo fmt --check", f"cd {absent} && git push -u origin HEAD"):
            result = _run_signal(script, command)
            if result.returncode != 0:
                raise AssertionError(
                    f"{script} exited {result.returncode} on {command!r}. A signal that exits "
                    "non-zero is replaced by a failure record, and every word the policy "
                    f"compares against goes undefined.\n{result.stdout}\n{result.stderr}"
                )

    verdict = _run_signal(RUNTIME_EVIDENCE_SIGNALS[0], f"cd {absent} && git push -u origin HEAD")
    if verdict.stdout.strip() != "UNKNOWN":
        raise AssertionError(
            "a push redirected at a directory that is not a working tree of this repository must "
            f"answer UNKNOWN, not a verdict about the caller's own checkout; got "
            f"{verdict.stdout.strip()!r}"
        )

    run_runtime_evidence_long_input_checks()


# Long inputs, which is where a `printf ... | grep -q` goes wrong. `-q` exits at its first match, so
# a `printf` still writing past the pipe buffer hits a closed pipe; under `pipefail` that write error
# becomes the pipeline's status and the test reads the opposite of what grep found. Measured
# 2026-10-13 with the piped form: a tip naming 8000 paths under crates/ answered `NOTRUNTIME`, which
# never denies, and a padded push event redirected at an absent checkout skipped the resolver and
# was judged against the caller's own tree. Both signals are run twice, once with SIGPIPE at its
# default and once ignored: ignored is how a Rust parent such as cupcake can leave it, and it is the
# form that also prints `printf: write error: Broken pipe`.
LONG_INPUT_LINES = 8000
NOT_RUNTIME_ANSWERS = {
    ".cupcake/signals/runtime_evidence_for_head.sh": "NOTRUNTIME",
    ".cupcake/signals/runtime_evidence_note.sh": "no crate under crates/ changed since origin/main",
}


def _long_crate_diff_repo(root: Path) -> Path:
    """A throwaway repository whose tip names LONG_INPUT_LINES files under crates/.

    Built with plumbing, one blob under every path, so it writes no files to disk. It has no
    `origin/main`, so the signals read the tip's own `git show --name-only`.
    """
    repo = root / "long-diff"
    repo.mkdir()

    def git(*args: str, stdin: str | None = None) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            input=stdin,
            text=True,
            capture_output=True,
            check=True,
            timeout=SIGNAL_TIMEOUT_SECONDS,
        ).stdout.strip()

    git("init", "-q", "-b", "base", ".")
    git("config", "user.email", "selftest@example.invalid")
    git("config", "user.name", "selftest")
    git("config", "commit.gpgsign", "false")
    blob = git("hash-object", "-w", "--stdin", stdin="seed\n")
    git("update-index", "--add", "--cacheinfo", f"100644,{blob},seed")
    base = git("commit-tree", "-m", "the base", git("write-tree"))
    git(
        "update-index",
        "--index-info",
        stdin="".join(
            f"100644 blob {blob}\tcrates/er-many/src/file-{i:05d}.rs\n"
            for i in range(1, LONG_INPUT_LINES + 1)
        ),
    )
    tip = git("commit-tree", "-p", base, "-m", "many crate paths", git("write-tree"))
    git("update-ref", "HEAD", tip)
    return repo


def _run_signal_with(
    script: str, event: str, cwd: Path, env: dict[str, str], ignore_sigpipe: bool
) -> subprocess.CompletedProcess[str]:
    argv = ["bash", str(REPO_ROOT / script)]
    if ignore_sigpipe:
        # `exec` keeps an ignored disposition, and bash cannot un-ignore a signal it starts with.
        argv = ["bash", "-c", "trap '' PIPE; exec bash \"$0\"", str(REPO_ROOT / script)]
    return subprocess.run(
        argv,
        cwd=cwd,
        input=event,
        text=True,
        capture_output=True,
        check=False,
        timeout=SIGNAL_TIMEOUT_SECONDS,
        env=env,
    )


def run_runtime_evidence_long_input_checks() -> None:
    absent = "/nonexistent-checkout-cupcake-signal-contract"
    with tempfile.TemporaryDirectory(prefix="cupcake-long-input-") as tmp:
        root = Path(tmp)
        repo = _long_crate_diff_repo(root)
        # A run root holding one run whose log has no build line, and an empty game directory: the
        # scan can measure, and nothing in it names the tip. Neither is this machine's real logs.
        (root / "runs" / "br-nobuild").mkdir(parents=True)
        (root / "runs" / "br-nobuild" / "er-thing.log").write_text("some other log\n")
        (root / "nogame").mkdir()
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("GIT_") and not k.startswith("CUPCAKE_RUNTIME_EVIDENCE")
        }
        env["ER_ME3_RUN_ROOT"] = str(root / "runs")
        env["ER_GAME_DIR"] = str(root / "nogame")

        # The padding is its own JSON lines after the command, so `grep` meets `push` long before
        # the end of its input. One long line would not do it: grep reads a whole line first.
        padded_push = json.dumps(
            {
                **json.loads(_signal_event(f"cd {absent} && git push -u origin HEAD")),
                "padding": ["x" * 40] * LONG_INPUT_LINES,
            },
            indent=1,
        )
        for ignore_sigpipe in (False, True):
            mode = "SIGPIPE ignored" if ignore_sigpipe else "SIGPIPE default"
            for script, not_runtime in NOT_RUNTIME_ANSWERS.items():
                result = _run_signal_with(
                    script, _signal_event("cargo fmt --check"), repo, env, ignore_sigpipe
                )
                answer = result.stdout.strip()
                if result.returncode != 0 or answer == not_runtime or "Broken pipe" in result.stderr:
                    raise AssertionError(
                        f"{script} ({mode}) on a tip naming {LONG_INPUT_LINES} paths under crates/ "
                        f"exited {result.returncode} and answered {answer!r}; it must exit 0, must "
                        f"not answer {not_runtime!r}, and must not report a broken pipe.\n"
                        f"{result.stderr}"
                    )
            push = _run_signal_with(
                RUNTIME_EVIDENCE_SIGNALS[0], padded_push, REPO_ROOT, env, ignore_sigpipe
            )
            if push.stdout.strip() != "UNKNOWN" or "Broken pipe" in push.stderr:
                raise AssertionError(
                    f"{RUNTIME_EVIDENCE_SIGNALS[0]} ({mode}) on a padded push redirected at "
                    f"{absent} answered {push.stdout.strip()!r}; it must answer UNKNOWN without "
                    f"reporting a broken pipe.\n{push.stderr}"
                )


HOST_PLATFORM_SIGNAL = ".cupcake/signals/host_platform.sh"
HOST_PLATFORM_ANSWERS = {"wsl", "native", "unknown"}


def run_host_platform_signal_checks() -> None:
    """The host_platform signal must answer one known word, and never `native` on a WSL kernel.

    `block_manual_pgrep` stands down only on `native`. Every other answer keeps the guard, so the
    dangerous failure is the one direction this checks: a WSL host reported as native, which would
    switch the WSL false-negative guard off exactly where it is needed. The cases in `main()` pin
    the answer through the override, so they cannot see what the script does unpinned; this does.
    """
    env = {k: v for k, v in os.environ.items() if k != "CUPCAKE_HOST_PLATFORM_OVERRIDE"}
    result = subprocess.run(
        [str(REPO_ROOT / HOST_PLATFORM_SIGNAL)],
        cwd=REPO_ROOT,
        input=_signal_event("true"),
        text=True,
        capture_output=True,
        check=False,
        timeout=SIGNAL_TIMEOUT_SECONDS,
        env=env,
    )
    answer = result.stdout.strip()
    if result.returncode != 0 or answer not in HOST_PLATFORM_ANSWERS:
        raise AssertionError(
            f"{HOST_PLATFORM_SIGNAL} exited {result.returncode} with {answer!r}; it must exit 0 "
            f"with one of {sorted(HOST_PLATFORM_ANSWERS)}.\n{result.stderr}"
        )
    osrelease = Path("/proc/sys/kernel/osrelease")
    if osrelease.is_file() and "microsoft" in osrelease.read_text(errors="replace").lower():
        if answer != "wsl":
            raise AssertionError(
                f"{HOST_PLATFORM_SIGNAL} answered {answer!r} on a WSL kernel "
                f"({osrelease.read_text(errors='replace').strip()!r}); it must answer 'wsl', or the "
                "pgrep guard stands down on the one kind of host it exists for."
            )
    for pinned in ("wsl", "native"):
        pinned_result = subprocess.run(
            [str(REPO_ROOT / HOST_PLATFORM_SIGNAL)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=SIGNAL_TIMEOUT_SECONDS,
            env={**env, "CUPCAKE_HOST_PLATFORM_OVERRIDE": pinned},
        )
        if pinned_result.stdout.strip() != pinned:
            raise AssertionError(
                f"{HOST_PLATFORM_SIGNAL} ignored CUPCAKE_HOST_PLATFORM_OVERRIDE={pinned!r} "
                f"(answered {pinned_result.stdout.strip()!r}); the regression cases depend on it."
            )


def run_signal_executable_checks() -> None:
    """Every script in `.cupcake/signals/` must be executable, with a shebang.

    A signal placed in that directory is auto-discovered and run as the command
    `./.cupcake/signals/<name>.sh` -- directly, not through `bash`. Without the executable bit the
    kernel refuses the exec and cupcake records exit code 126, and a signal that exits non-zero is
    not delivered to the policy as its output at all: the string is replaced by a failure record,
    `{"error", "exit_code", "output", "success"}`. Every string comparison the policy makes against
    a word is then undefined, the rule body fails, and the decision set comes back empty. Cupcake
    reports a clean allow and exits 0.

    So one missing `chmod +x` turns a guard off in production while `opa test` stays green, and
    nothing anywhere says so. Measured 2026-09-16 on `frida_evidence.sh`, which shipped without the
    bit: `cupcake eval` allowed a `crates/**/*.rs` edit carrying no measurement at all, which is the
    single thing that policy exists to refuse.

    Fixing the one file does not close the class. This does.
    """
    signals = sorted((REPO_ROOT / ".cupcake" / "signals").glob("*.sh"))
    if not signals:
        raise AssertionError(
            ".cupcake/signals/ holds no *.sh at all. Either the directory moved or this gate is "
            "watching the wrong place; an empty walk makes every signal innocent."
        )
    for script in signals:
        rel = script.relative_to(REPO_ROOT)
        if not os.access(script, os.X_OK):
            raise AssertionError(
                f"{rel} is not executable. Cupcake auto-discovers it and execs it directly, so "
                "the kernel refuses with exit code 126, cupcake replaces its output with a "
                "failure record, and every policy reading it silently allows. Run "
                f"`chmod +x {rel}`."
            )
        first = script.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        if not first or not first[0].startswith("#!"):
            raise AssertionError(
                f"{rel} has no shebang. Exec'd directly, it is the interpreter line that decides "
                "what runs it; without one the exec fails the same way a missing bit does."
            )


def run_orphaned_rego_suites() -> None:
    if not shutil.which("opa"):
        print("skip: orphaned rego suites (no opa on PATH)")
        return
    for suite in ORPHANED_REGO_SUITES:
        result = subprocess.run(
            ["opa", "test", *(str(REPO_ROOT / part) for part in suite)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=OPA_TEST_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"opa test failed for {suite[-1]}:\n{result.stdout}\n{result.stderr}"
            )


# This gate does not reliably fit in a 30-second foreground shell. Run it in the background.
#
# Not a caveat -- a measurement. The work is 176 `cupcake eval` spawns costing ~237 CPU-seconds in
# total, and `cupcake eval` takes one event on stdin per process (checked against `--help`: there is
# no batch or server mode), so that CPU cost is a floor, not an inefficiency. Wall clock is therefore
# just that floor divided by however many cores the rest of the box leaves free, and on a machine six
# agents share that is not a quantity this script controls. Three runs of this code, same day, same
# tree: 20.4s at 1071% CPU, 23.4s at 1071%, 35.0s at 731%.
#
# An agent that runs this in a capped foreground shell gets SIGKILLed at 30 seconds, which is
# indistinguishable from a hang -- and the conclusion an agent then draws ("the gate is broken", or
# worse, "the policies are broken") is wrong in the dangerous direction. check.sh classifies such a
# kill as inconclusive rather than a pass for exactly this reason.
#
# So the requirement announces itself, on stdout, flushed, before any work starts: an agent that is
# about to be killed has already been told why. Being killed after reading this line is a correctly
# reported environment limit; being killed without it is a mystery each agent has to re-solve.
FOREGROUND_CAP_NOTICE = (
    "test-cupcake-policies: ~20-35s (176 `cupcake eval` spawns, ~237 CPU-seconds).\n"
    "test-cupcake-policies: THIS CAN EXCEED A 30s FOREGROUND CAP. Run it in the background;\n"
    "test-cupcake-policies: a kill at 30s is the cap, NOT a hang and NOT a policy failure."
)


def main() -> int:
    # Flushed, and first: buffered output does not survive SIGKILL, and this notice is worth
    # nothing if the cap eats it.
    print(FOREGROUND_CAP_NOTICE, flush=True)
    # First and cheapest: a signal that cannot be exec'd turns its policies off in production while
    # every other gate here stays green, so there is no point spending 237 CPU-seconds before
    # asking whether the signals can run at all.
    run_signal_executable_checks()
    run_host_platform_signal_checks()
    run_orphaned_rego_suites()
    run_runtime_evidence_signal_checks()
    cases = [
        PolicyCase("allow-rtk", "rtk ls", True),
        # Commit and lint gates (user directive 2026-10-17): closed without the user's
        # `authorize commit`, open with it. scripts/test-commit-authorization-signal.py has the
        # transcript cases; these pin the wiring through the whole rulebook.
        PolicyCase(
            "deny-commit-without-authorization",
            "git commit -m 'feat: x'",
            False,
            "ask the user to reply with exactly: `authorize commit`",
            commit_authorization=False,
        ),
        PolicyCase(
            "deny-worktree-commit-without-authorization",
            "git -C /home/banon/projects/er-mods-rs/.worktrees/portrait-stats-crate commit -m 'x'",
            False,
            "`authorize commit`",
            commit_authorization=False,
        ),
        PolicyCase("allow-commit-with-authorization", "git commit -m 'feat: x'", True),
        PolicyCase(
            "deny-chained-commit-with-authorization",
            "git add -A && git commit -m 'feat: x'",
            False,
            "does not commit without",
        ),
        PolicyCase(
            "deny-cargo-fmt-without-authorization",
            "cargo fmt -p er-quickload",
            False,
            "Lints and formatters run only when the user is about to commit",
            commit_authorization=False,
        ),
        PolicyCase(
            "deny-comment-caps-fix-without-authorization",
            "python3 scripts/check-comment-caps.py --fix crates/er-quickload/src/lib.rs",
            False,
            "Lints and formatters run only when the user is about to commit",
            commit_authorization=False,
        ),
        PolicyCase("allow-cargo-fmt-with-authorization", "cargo fmt -p er-quickload -- --check", True),
        PolicyCase(
            "allow-comment-caps-selftest-without-authorization",
            "python3 scripts/check-comment-caps.py --selftest",
            True,
            commit_authorization=False,
        ),
        PolicyCase(
            "allow-cargo-test-without-authorization",
            "cargo test -p er-soulsformats",
            True,
            commit_authorization=False,
        ),
        # no_grep_for_build_errors through the live engine: a grep over `ls` output in another
        # statement from a script named er-builds-* is not a build adjudicated by grep (2026-10-01),
        # while the cargo-grep that created the policy still is.
        PolicyCase(
            "allow-grep-over-ls-beside-builds-named-script",
            "ls scripts | grep -i -E 'er-builds|er-mechanics|ash' ; "
            "python3 scripts/er-builds-ash-choice.py --help 2>&1 | head -40",
            True,
        ),
        PolicyCase(
            "deny-grep-over-cargo-check-output",
            "cargo check -p er-quickload 2>&1 | grep -E 'error' -A6 | head -20",
            False,
            "A build reports success or failure ONCE",
        ),
        # 2026-10-15: a grep filtering git's path list inside a substitution, which a gate then
        # reads as arguments, is not a grep over the gate's output. Live because the substitution
        # token and the quote reading beside it are regexes the wasm runtime runs, not `opa test`.
        # The measured command named `crates/er-cel-shade`; the fixture names a crate that does not
        # exist, because check-stages.py reads a real crate path in this file as an input of the
        # policy stage.
        PolicyCase(
            "allow-gate-reading-a-git-path-list-filtered-in-a-substitution",
            "files=$( (git diff --name-only 72ef6a9b; git ls-files --others --exclude-standard) "
            "| sort -u | grep -E '^(crates/er-shade-fixture|crates/er-compiler-fixture|scripts)/' "
            "| while read f; do [ -f \"$f\" ] && echo \"$f\"; done ); echo \"$files\" | wc -l; "
            "python3 scripts/check-comment-caps.py $files; echo \"exit=$?\"",
            True,
        ),
        PolicyCase(
            "allow-gate-taking-a-filtered-git-path-list-as-arguments",
            "python3 scripts/check-comment-caps.py "
            "$(git diff --name-only 72ef6a9b | grep -E '^(crates/er-shade-fixture|scripts)/'); "
            "echo \"exit=$?\"",
            True,
        ),
        PolicyCase(
            "deny-build-in-a-substitution-whose-output-is-grepped",
            "echo $(cargo build 2>&1) | grep error",
            False,
            "A build reports success or failure ONCE",
        ),
        PolicyCase(
            "allow-local-shell-vars-before-commands-with-coarse-ast",
            "run_id=$(date +%Y%m%d-%H%M%S)\n"
            "log_dir=\"target/runtime-probe/profile-portrait-capture-measure-$run_id\"\n"
            "mkdir -p \"$log_dir\"\n"
            "touch .auto/run_profile_portrait_capture_once\n"
            "nohup ./.auto/measure.sh > \"$log_dir/measure.out\" 2> \"$log_dir/measure.err\" &\n"
            "pid=$!\n"
            "echo \"$pid\" > \"$log_dir/measure.pid\"\n"
            "echo \"$log_dir\"",
            True,
            None,
            {
                "command_ast": {
                    "parse_ok": True,
                    "statements": [
                        {
                            "env_setting": True,
                            "command_name": "mkdir",
                        }
                    ],
                }
            },
        ),
        PolicyCase(
            "allow-shell-variable-bookkeeping",
            "./scripts/check-no-timeouts.py\nrc=$?\necho \"$rc\"",
            True,
        ),
        PolicyCase(
            "allow-ast-shell-variable-bookkeeping",
            "rc=$?",
            True,
            None,
            {
                "command_ast": {
                    "parse_ok": True,
                    "statements": [
                        {
                            "env_setting": True,
                            "command_name": None,
                        }
                    ],
                }
            },
        ),
        PolicyCase(
            "allow-flattened-shell-variable-bookkeeping",
            "set +e false rc=$? set -e echo \"$rc\"",
            True,
        ),
        PolicyCase(
            "allow-python-heredoc-with-overbroad-affected-root",
            "python3 - <<'PY'\nprint(1)\nPY",
            True,
            extra_event={"affected_parent_directories": ["/"]},
        ),
        PolicyCase(
            "allow-repo-cupcake-system-path-not-absolute-system",
            "opa check .cupcake/system .cupcake/policies/claude/builtins/protected_paths.rego",
            True,
        ),
        # BUILTIN-protected-paths-parent vs a Rust file authored through a heredoc
        # (2026-08-31). These have to run here and not only under `opa test`: the
        # engine's `whitespace_normalization` enrichment replaces every newline in
        # the command with a space before any policy runs, so the heredoc body
        # arrives welded onto the `cat` that reads it and the policy's own
        # line-wise payload split has nothing to split on. Under `opa test` the
        # raw multi-line text hides that entirely, which is how the guard came to
        # deny an ordinary Rust doc comment for a month with a green suite:
        #
        #   "System path modification blocked by policy
        #    (/System/ would be affected by operation on /)"
        #
        # because the prose word "Install" satisfied the destructive-verb test and
        # the `///` satisfied the root-path test. Verbs are now required to stand
        # in command position; the deny cases below pin that this did not cost the
        # rule anything that actually runs.
        # Written to `build-support/`, not `crates/`, since 2026-09-22: it is still a Rust file
        # authored through a heredoc, which is what this case is about, and the Frida gate now
        # sees the Bash spelling of a `crates/**/*.rs` write and correctly refuses it (bd
        # er-effects-rs-wuij). Keeping the old path would have tested that guard instead of
        # this one, and would have made the case's verdict depend on whether this checkout
        # happens to carry a measurement.
        PolicyCase(
            "allow-rust-doc-comment-heredoc-with-prose-verb",
            "cat > build-support/demo.rs <<'EOF'\n"
            "/// Install the detour into the game image.\n"
            "///\n"
            "/// Truncate the log first; the caller moves the old one aside.\n"
            "pub fn install_hook() {}\n"
            "EOF",
            True,
        ),
        PolicyCase(
            "allow-markdown-heredoc-naming-an-absolute-path-in-prose",
            "cat > docs/demo.md <<'EOF'\n"
            "Install the binary to /usr/local/bin when you are done.\n"
            "EOF",
            True,
        ),
        PolicyCase(
            "deny-root-recursive-delete",
            "rm -rf /",
            False,
            "would be affected by operation on",
        ),
        # The separator is written out rather than left to a newline: this runner
        # calls `cupcake eval` directly, so scripts/cupcake-hook.sh is not in the
        # path to rewrite an unquoted newline to "; " (bd er-effects-rs-5eah) and
        # line 2 would arrive with no boundary in front of it.
        PolicyCase(
            "deny-root-delete-after-heredoc-terminator",
            "git commit -q -F - <<'EOF'\n"
            "message text with a bare / in it\n"
            "EOF\n"
            "; rm -rf /",
            False,
            "would be affected by operation on",
        ),
        PolicyCase(
            "deny-sudo-prefixed-root-delete",
            "sudo rm -rf /",
            False,
            "would be affected by operation on",
        ),
        # --- Destructive payloads inside a shell wrapper (2026-08-31) --------
        #
        # Measured against this same live engine before the fix: all seventeen
        # wrapper spellings below came back allow. Two causes had to be answered
        # together, which is why they must be pinned here and not only under
        # `opa test`:
        #
        #   * the verb is not in the outer command's command position, and
        #     commands.has_verb could not see it either (its `(^|\s)` anchor
        #     never matched `"rm`);
        #   * `affected_parent_directories` -- which the parent rule pairs its
        #     verb test with -- does not contain the payload's target. The
        #     preprocessor reads the quoted payload as a path operand of `bash`,
        #     so the event carries ["<cwd>/rm -rf "] and never "/". Only the
        #     live engine supplies that field, so an interpreter test cannot
        #     show whether the deny is reachable in production.
        #
        # Assembled from parts so that editing this file through a Bash command
        # does not hand the guards a literal root delete to read.
        PolicyCase(
            "deny-bash-c-double-quoted-root-delete",
            'bash -c "' + ROOT_DELETE + '"',
            False,
            "inside a shell-wrapper payload",
        ),
        PolicyCase(
            "deny-bash-c-single-quoted-root-delete",
            "bash -c '" + ROOT_DELETE + "'",
            False,
            "inside a shell-wrapper payload",
        ),
        PolicyCase(
            "deny-sh-c-root-delete",
            "sh -c '" + ROOT_DELETE + "'",
            False,
            "inside a shell-wrapper payload",
        ),
        # fish is the wrapper AGENTS.md tells agents to use for this box, and it
        # was not even on the shell-name list before today.
        PolicyCase(
            "deny-fish-c-root-delete",
            "fish -c '" + ROOT_DELETE + "'",
            False,
            "inside a shell-wrapper payload",
        ),
        PolicyCase(
            "deny-sudo-wrapped-root-delete",
            "sudo bash -c '" + ROOT_DELETE + "'",
            False,
            "inside a shell-wrapper payload",
        ),
        PolicyCase(
            "deny-xargs-wrapped-root-delete",
            "xargs -I{} sh -c '" + ROOT_DELETE + "'",
            False,
            "inside a shell-wrapper payload",
        ),
        # Nesting terminates at three levels; two is the deepest literal quoting
        # reaches without escapes, and escaped quotes are stripped before the
        # split, so `bash -c "bash -c \"...\""` cannot recurse further.
        PolicyCase(
            "deny-nested-wrapper-root-delete",
            "bash -c \"bash -c '" + ROOT_DELETE + "'\"",
            False,
            "inside a shell-wrapper payload",
        ),
        PolicyCase(
            "deny-wrapped-root-glob-delete",
            "bash -c 'rm -rf /*'",
            False,
            "inside a shell-wrapper payload",
        ),
        PolicyCase(
            "deny-wrapped-root-recursive-chmod",
            "bash -c 'chmod -R 777 /'",
            False,
            "inside a shell-wrapper payload",
        ),
        PolicyCase(
            "deny-wrapped-find-root-delete",
            "bash -c 'find / -name x -delete'",
            False,
            "inside a shell-wrapper payload",
        ),
        PolicyCase(
            "deny-wrapped-root-delete-in-second-segment",
            "bash -c 'echo hi; " + ROOT_DELETE + "'",
            False,
            "inside a shell-wrapper payload",
        ),
        # ... and the over-approximations the rule must not make. A payload that
        # destroys something outside every protected path stays allowed: the
        # ancestor `/` must not turn every absolute operand into a root
        # operation.
        PolicyCase("allow-wrapped-read-of-root", "bash -c 'ls /'", True),
        PolicyCase("allow-wrapped-relative-delete", "bash -c 'rm -rf target/x'", True),
        PolicyCase(
            "allow-wrapped-absolute-delete-outside-protected-paths",
            "bash -c 'rm -rf /home/banon/scratch'",
            True,
        ),
        PolicyCase(
            "allow-wrapped-absolute-copy-outside-protected-paths",
            "bash -c 'cp a.txt /home/banon/b.txt'",
            True,
        ),
        # The unwrapped `cp file ~` is allowed (the preprocessor does not expand
        # the tilde, so it reports `<cwd>/~`), and the wrapped form must not be
        # held to a stricter standard than the command it wraps.
        PolicyCase("allow-wrapped-copy-into-home", "bash -c 'cp file ~'", True),
        # A benign wrapped build stays allowed. The example carries a `-p`
        # because ER-effects-require-SCOPED-cargo (2026-09-02) denies an
        # unscoped cargo invocation wherever it appears, `bash -c` payloads
        # included -- so the unscoped spelling this case used to carry now
        # fails for a reason that has nothing to do with what the case is
        # about. What it is about is the shell-wrapper rule not
        # over-approximating: a harmless payload must survive being wrapped.
        # Scoping the build keeps that the only thing under test.
        PolicyCase(
            "allow-wrapped-build",
            "bash -c 'cargo build --release -p er-quickload'",
            True,
        ),
        PolicyCase("allow-echo-of-a-root-delete", "echo '" + ROOT_DELETE + "'", True),
        # --- Known-open residue, pinned so it is visible ---------------------
        #
        # A guard that half-catches wrapper payloads is worse than one that
        # visibly does not, because it invites reliance. Everything below is
        # still allowed after the 2026-08-31 wrapper rule, on purpose, and is
        # pinned here so the boundary is a test rather than a belief. If one of
        # these ever goes red, the rule got stronger and the pin should flip --
        # it must never be deleted to make the suite quiet.
        #
        # 1. A payload the decomposition cannot read. Failing closed would deny
        #    every `bash -c "$VAR"`, and unlike the git guards there is no second
        #    signal to narrow it: an opaque variable names no path at all.
        PolicyCase("known-open-opaque-wrapper-payload", "bash -c $CMD", True),
        PolicyCase("known-open-substituted-wrapper-payload", "bash -c $(echo hi)", True),
        # 2. Three levels of escaped nesting. shell_payloads_deep unrolls three
        #    levels, but escaped quotes are stripped before the split, so the
        #    innermost payload loses its quoting and never becomes a text of its
        #    own -- the decomposition terminates (which is the property that
        #    matters) and yields a payload whose verb is behind a `bash -c` that
        #    command position does not follow.
        PolicyCase(
            "known-open-triple-nested-escaped-wrapper-payload",
            "bash -c \"bash -c 'bash -c \\\"" + ROOT_DELETE + "\\\"'\"",
            True,
        ),
        # 3. `ssh host '<program>'` is not decomposed at all, and that is a
        #    decision: the payload runs on another machine, so denying it for
        #    endangering this host's /etc would be a guard that is wrong on
        #    purpose.
        PolicyCase("known-open-remote-shell-payload", "ssh host '" + ROOT_DELETE + "'", True),
        # 4. An interpreter payload that destroys the root without naming a
        #    configured protected path. BUILTIN-protected-paths-script is
        #    mention-based by design, and widening it to ancestors would deny
        #    `python3 -c "print('/')"` -- the `/` sits between two quotes, which
        #    is a path boundary. Matching the existing precedent rather than
        #    inventing a broader one.
        PolicyCase(
            "known-open-interpreter-payload-targeting-root",
            "python3 -c \"import shutil; shutil.rmtree('/')\"",
            True,
        ),
        # 5. A tilde or $home target, wrapped or not. The preprocessor does not
        #    expand either, so the UNWRAPPED command is allowed too; the wrapper
        #    rule deliberately declines to be stricter than what it wraps.
        PolicyCase("known-open-wrapped-tilde-delete", "bash -c 'rm -rf ~'", True),
        PolicyCase("known-open-unwrapped-tilde-delete", "rm -rf ~", True),
        # `echo $(rm -rf /)` -- the OPA suite pinned this as denied and it was
        # allowed live, because the suite hand-fed affected_parent_directories
        # ["/"] while the real preprocessor reports ["<cwd>/$(rm", "/)"] and `/)`
        # is a parent of nothing. separator_trimmed_dir now trims `)` too.
        PolicyCase(
            "deny-root-delete-inside-command-substitution",
            "echo $(" + ROOT_DELETE + ")",
            False,
            "would be affected by operation on",
        ),
        PolicyCase(
            "deny-git-push-on-main",
            "git push",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        PolicyCase(
            "deny-git-push-main-from-feature",
            "git push origin HEAD:main",
            False,
            "Do not push directly to main",
        ),
        # The ten cases below were written as `allow-*` against `git_block_main_push`, whose job is
        # narrower than the rule now in force: it asks which ref a push targets, and lets a feature
        # branch, a worktree, a rename refspec and a deletion through. `git_block_any_push` asks
        # nothing -- no push this agent makes is allowed, so every one of those exceptions is now
        # unreachable from the agent's path and the cases assert the deny instead.
        #
        # Renamed rather than deleted. Each still names the shape it was written for, so the
        # coverage `git_block_main_push` has for that shape is still located from here, and a later
        # session can see that the narrow guard's exceptions were superseded rather than lost.
        PolicyCase(
            "any-push-guard-denies-feature-branch-push",
            "git push -u origin guard/no-direct-main-push",
            False,
            "The agent does not push",
        ),
        PolicyCase("deny-stale-origin-main-rebase", "git rebase origin/main", False, "origin/main is stale or could not be verified", extra_event={"signals": {"origin_main_oids": "a" * 40 + " " + "b" * 40}}),
        PolicyCase("allow-fresh-origin-main-rebase", "git rebase origin/main", True, extra_event={"signals": {"origin_main_oids": "a" * 40 + " " + "a" * 40}}),
        PolicyCase("deny-stale-force-with-lease", "git push --force-with-lease origin feature/x", False, "origin/main is stale or could not be verified", extra_event={"signals": {"origin_main_oids": "a" * 40 + " " + "b" * 40}}),
        # Worktree-target exception: `git -C <registered non-main worktree>`
        # commit/push is allowed even when the session checkout sits on main
        # (bd guard-blocks-worktree-commits-from-main-session-cwd-2026-07-29).
        PolicyCase(
            "allow-git-c-commit-nonmain-worktree-from-main-session",
            'git -C /home/banon/projects/er-mods-rs/.worktrees/portrait-stats-crate commit -m "ok"',
            True,
            extra_event={
                "signals": {
                    "current_branch": "main\n",
                    "worktree_branches": WORKTREE_FIXTURE,
                }
            },
        ),
        PolicyCase(
            "deny-git-c-commit-unregistered-path-from-main-session",
            'git -C /tmp/not-a-worktree commit -m "bad"',
            False,
            "Do not commit unless",
            extra_event={
                "signals": {
                    "current_branch": "main\n",
                    "worktree_branches": WORKTREE_FIXTURE,
                }
            },
        ),
        PolicyCase(
            "any-push-guard-denies-git-c-push-from-nonmain-worktree",
            "git -C /home/banon/projects/er-mods-rs/.worktrees/portrait-stats-crate push -u origin feature/portrait-stats-crate",
            False,
            "The agent does not push",
            extra_event={
                "signals": {
                    "current_branch": "main\n",
                    "worktree_branches": WORKTREE_FIXTURE,
                }
            },
        ),
        PolicyCase(
            "deny-git-c-push-main-refspec-from-nonmain-worktree",
            "git -C /home/banon/projects/er-mods-rs/.worktrees/portrait-stats-crate push origin HEAD:main",
            False,
            "Do not push directly to main",
            extra_event={
                "signals": {
                    "current_branch": "main\n",
                    "worktree_branches": WORKTREE_FIXTURE,
                }
            },
        ),
        # Source:destination refspec exception (2026-08-25). Renaming an
        # already-pushed remote branch names a non-main destination explicitly,
        # so it cannot update remote main -- but it matched neither earlier
        # exception's parser and was denied from a session sitting on main.
        PolicyCase(
            "any-push-guard-denies-refspec-rename",
            "git push origin origin/refactor/drop-dead-gates:refs/heads/split/drop-dead-gates",
            False,
            "The agent does not push",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        # ... and every main destination stays denied through it. push_targets_main
        # is a separate blocked_push_context rule, so no exception can reach it.
        PolicyCase(
            "deny-git-push-refspec-to-refs-heads-main-from-main-session",
            "git push origin origin/refactor/drop-dead-gates:refs/heads/main",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        # `heads/main` resolves to refs/heads/main on the remote (verified against
        # real repositories); from a feature branch only push_targets_main can
        # catch it, which is why it was added there.
        PolicyCase(
            "deny-git-push-refspec-to-heads-main-from-feature-branch",
            "git push origin HEAD:heads/main",
            False,
            "Do not push directly to main",
        ),
        PolicyCase(
            "deny-git-push-refspec-rename-chained-with-main-push",
            "git push origin origin/a:refs/heads/split/a && git push origin main",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        # Deletion pushes (2026-09-14). Both spellings used to fail closed; they
        # are now read by pushes_target_only_deletions, which is written in token
        # operations rather than a pattern precisely so that it cannot repeat the
        # wasm regex trap this runner exists to catch (bd
        # a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14).
        # These cases have to run here and not only under `opa test`: a green
        # interpreter suite says nothing about the runtime that ships.
        PolicyCase(
            "any-push-guard-denies-deletion-refspec",
            "git push origin :refs/heads/split/a",
            False,
            "The agent does not push",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        PolicyCase(
            "any-push-guard-denies-delete-option",
            "git push origin --delete refactor/experiments-split",
            False,
            "The agent does not push",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        PolicyCase(
            "any-push-guard-denies-delete-of-multiple-branches",
            "git push origin --delete stale/one stale/two",
            False,
            "The agent does not push",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        # Every main spelling stays denied through the new exception, and the last
        # of these runs from a feature branch where only push_targets_main -- a
        # blocked_push_context rule carrying no exception guard -- can catch it.
        PolicyCase(
            "deny-git-push-delete-option-main-from-main-session",
            "git push origin --delete main",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        PolicyCase(
            "deny-git-push-deletion-refspec-main-from-main-session",
            "git push origin :refs/heads/main",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        PolicyCase(
            "deny-git-push-delete-option-main-from-feature-branch",
            "git push origin --delete heads/main",
            False,
            "Do not push directly to main",
        ),
        # A deletion mixed with any other push is not vouched for: the count-match
        # is taken across the whole command.
        PolicyCase(
            "deny-git-push-deletion-chained-with-bare-push",
            "git push origin --delete stale/one && git push",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        # An option the parser does not read leaves the command unrecognised, so
        # `--force` alongside a deletion fails closed rather than riding along.
        PolicyCase(
            "deny-git-push-force-with-delete-option",
            "git push --force origin --delete stale/one",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        # A second operand the parser never read must not be vouched for.
        PolicyCase(
            "deny-git-push-refspec-with-second-operand-from-main-session",
            "git push origin origin/a:refs/heads/split/a origin/b:refs/heads/split/b",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        # --- Shell-wrapper payloads (2026-08-26, bd er-effects-rs-dt2e) ------
        #
        # Measured against this same live engine before the fix: every one of the
        # deny cases below came back allow with zero denials. The four git guards
        # anchored their patterns on a separator class that contains `\n` but not
        # a quote, so a payload inside `bash -c '...'` had no command position --
        # and AGENTS.md tells agents to wrap commands exactly that way for fish.
        #
        # The .rego unit tests run in the OPA interpreter; these run the real
        # binary, which compiles to WASM and has silently dropped whole guards
        # before. Both halves are needed.
        PolicyCase(
            "deny-bash-c-single-quoted-push-main",
            "bash -c 'git push origin main'",
            False,
            "Do not push directly to main",
        ),
        PolicyCase(
            "deny-sh-c-double-quoted-push-main",
            'sh -c "git push origin main"',
            False,
            "Do not push directly to main",
        ),
        PolicyCase(
            "deny-bash-lc-push-head-to-main",
            "bash -lc 'git push origin HEAD:main'",
            False,
            "Do not push directly to main",
        ),
        PolicyCase(
            "deny-zsh-c-push-main",
            'zsh -c "git push origin main"',
            False,
            "Do not push directly to main",
        ),
        PolicyCase(
            "deny-nested-wrapper-push-main",
            "bash -c 'bash -c \"git push origin main\"'",
            False,
            "Do not push directly to main",
        ),
        PolicyCase(
            "deny-bash-c-bare-push-from-main-session",
            "bash -c 'git push'",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        # An exception cannot vouch for a command that also hides a push in a
        # wrapper: the count-match spans every executed text at once.
        PolicyCase(
            "deny-explicit-upstream-push-chained-with-wrapped-bare-push",
            "git push -u origin feature/x && bash -c 'git push'",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        # ... and the refspec-rename exception still works through a wrapper,
        # which is what makes the decomposition symmetric rather than just stricter.
        PolicyCase(
            "any-push-guard-denies-wrapped-refspec-rename",
            "bash -c 'git push origin origin/a:refs/heads/split/a'",
            False,
            "The agent does not push",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        PolicyCase(
            "deny-wrapped-refspec-to-main-from-main-session",
            "bash -c 'git push origin origin/a:refs/heads/main'",
            False,
            "Do not push directly to main",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        # A payload the guard cannot read must not become an implicit allow.
        PolicyCase(
            "deny-unreadable-wrapper-payload-naming-git-and-push",
            "bash -c $GIT_PUSH_CMD",
            False,
            "cannot read",
        ),
        # ... but an opaque wrapper outside the guard's jurisdiction is noise.
        PolicyCase(
            "allow-unreadable-wrapper-payload-unrelated-to-git",
            "bash -c $BUILD_CMD",
            True,
        ),
        # Siblings that shared the same anchor construction.
        PolicyCase(
            "deny-bash-c-commit-from-main-session",
            "bash -c 'git commit -m bad'",
            False,
            "Do not commit unless",
            extra_event={"signals": {"current_branch": "main\n"}},
        ),
        PolicyCase(
            "deny-bash-c-force-push-when-origin-main-stale",
            "bash -c 'git push --force origin feature/x'",
            False,
            "origin/main is stale or could not be verified",
            extra_event={"signals": {"origin_main_oids": "a" * 40 + " " + "b" * 40}},
        ),
        PolicyCase(
            "deny-bash-c-rebase-onto-stale-origin-main",
            "sh -c \"git rebase origin/main\"",
            False,
            "origin/main is stale or could not be verified",
            extra_event={"signals": {"origin_main_oids": "a" * 40 + " " + "b" * 40}},
        ),
        PolicyCase(
            "deny-bash-c-commit-skipping-hooks",
            "bash -c 'git commit " + "--no" + "-verify -m bad'",
            False,
            "are not permitted",
        ),
        # --- Quoted text is not an executed payload --------------------------
        #
        # The mirror-image defect, and the reason widening the anchor class was
        # not the fix: `\n` is in that class, so a memory body, a commit message
        # or a doc that merely quoted the guarded command on its own line was
        # denied -- with nothing executed. This one was measured live: writing a
        # bd memory that documented this very hole was refused by both
        # ER-effects-block-main-push and ER-effects-block-main-commit. A guard
        # whose own documentation cannot be written in the repo that enforces it
        # is unwritable, so these are requirements, not niceties.
        PolicyCase(
            "allow-bd-memory-body-quoting-the-guarded-command",
            '$HOME/.local/bin/bd remember --key wrapper-bypass "before\ngit push origin main\nafter"',
            True,
        ),
        PolicyCase(
            "allow-commit-message-naming-the-guarded-command",
            'git commit -m "guard: block git push origin main via wrappers"',
            True,
        ),
        PolicyCase(
            "allow-commit-message-with-the-guarded-command-on-its-own-line",
            'git commit -m "guard: close the wrapper bypass\n\ngit push origin main was invisible\n"',
            True,
        ),
        PolicyCase(
            "allow-heredoc-documenting-the-guarded-command",
            "cat > docs/guards.md <<'EOF'\ngit push origin main\nEOF",
            True,
        ),
        PolicyCase(
            "allow-echo-of-the-guarded-command",
            'echo "git push origin main"',
            True,
        ),
        # A message that names the bypass FORM: splitting on `'` alone finds a
        # span whose preceding text ends in `bash -c `, so without a nesting
        # check the message body reads as an executed payload and the commit
        # describing the fix is denied by the fix. This is the exact shape of
        # the commit message that landed this change.
        PolicyCase(
            "allow-commit-message-quoting-the-wrapper-bypass-form",
            'git commit -m "the bypass form was bash -c \'git push origin main\'"',
            True,
        ),
        PolicyCase(
            "allow-commit-message-quoting-the-bypass-form-inverted-quotes",
            "git commit -m 'the bypass form was bash -c \"git push origin main\"'",
            True,
        ),
        # One apostrophe in a double-quoted body must not desynchronise the
        # quote parity and drop the whole command back to its raw form, where
        # the line-anchored mention would be denied again.
        PolicyCase(
            "allow-bd-memory-body-with-an-apostrophe",
            '$HOME/.local/bin/bd remember --key k "it\'s about this:\ngit push origin main\nend"',
            True,
        ),
        # `python3 -c` takes a Python program, not shell.
        PolicyCase(
            "allow-python-dash-c-string-literal-naming-the-command",
            "python3 -c 'print(\"git push origin main\")'",
            True,
        ),
        # Neutralising a quoted span blanks its command-position characters
        # rather than deleting it, so a quoted operand still parses.
        PolicyCase(
            "any-push-guard-denies-git-c-push-with-quoted-worktree-path",
            'git -C "/home/banon/projects/er-mods-rs/.worktrees/portrait-stats-crate" push -u origin feature/portrait-stats-crate',
            False,
            "The agent does not push",
            extra_event={
                "signals": {
                    "current_branch": "main\n",
                    "worktree_branches": WORKTREE_FIXTURE,
                }
            },
        ),
        # Fail-closed fallbacks: command substitution runs even inside double
        # quotes, so such a text keeps its raw form and the anchors keep matching.
        PolicyCase(
            "deny-command-substitution-running-a-push-to-main",
            'echo "$(git push origin main)"',
            False,
            "Do not push directly to main",
        ),
        # Not known-open any more -- but still allowed here, and the difference
        # is this runner, not the guard. Corrected 2026-08-31.
        #
        # The old note said production allowed this and no policy could change
        # that. Production denies it, and has since the hook shim landed:
        # scripts/cupcake-hook.sh sees the raw text before cupcake does, treats a
        # heredoc a shell reads as a program rather than data, and rewrites its
        # unquoted newlines to `; ` -- so the second line arrives with a
        # separator in front of it and the push guard fires. Measured both ways
        # on 2026-08-31: `bash scripts/cupcake-hook.sh` -> deny, `cupcake eval`
        # direct -> allow.
        #
        # This runner calls `cupcake eval` directly, so it never sees the
        # rewrite, and for every multi-line command it is asserting the verdict
        # of a path no command travels. The expectation below is therefore the
        # direct path's verdict, kept so the divergence stays visible rather than
        # being quietly "fixed" to the production answer this file cannot
        # observe. scripts/test-cupcake-delivered-shape.py runs the same command
        # through the shim and asserts the denial, and asserts this exact
        # divergence, so neither half can drift without something going red.
        PolicyCase(
            "direct-eval-only-shell-read-heredoc-push-main-denied-in-production",
            "bash <<'EOF'\ngit push origin main\nEOF",
            True,
        ),
        PolicyCase(
            "deny-destructive-parent-root",
            "rm -rf /",
            False,
            "would be affected by operation on /",
            extra_event={"affected_parent_directories": ["/"]},
        ),
        # An `=` inside a quoted argument is not an env assignment.
        PolicyCase(
            "allow-equals-in-quoted-grep",
            'rtk grep -n "FOO=bar|PROTON=x" .auto/runtime_probe.sh',
            True,
        ),
        PolicyCase(
            "allow-equals-in-double-quoted-echo",
            'echo "PATH=/usr/bin works"',
            True,
        ),
        PolicyCase(
            "allow-equals-in-heredoc-body",
            "python3 - <<'PY'\nimport os\nFOO=os.getpid()\nPY",
            True,
        ),
        # Quoted semicolons are not command separators (no command_ast supplied
        # at runtime, so the quote-stripping fallback must handle these).
        PolicyCase(
            "allow-semicolon-in-double-quoted-commit",
            'git commit -m "fix a; fix b"',
            True,
        ),
        PolicyCase(
            "allow-semicolon-in-python-dash-c",
            'python3 -c "import sys; print(sys.version)"',
            True,
        ),
        PolicyCase(
            "allow-semicolon-in-single-quoted-arg",
            "bd remember --key k 'first clause; second clause'",
            True,
        ),
        # Backslash-escaped quotes inside a quoted message must not desync the
        # quote-stripping (a commit message that quotes example commands).
        PolicyCase(
            "allow-escaped-quotes-with-semicolons",
            'git commit -m "guard ignores quotes; e.g. python3 -c \\"a; b\\" works"',
            True,
        ),
        # Heredoc bodies are interpreter input; their semicolons are not shell
        # separators (e.g. python statement separators inside python3 - <<'PY').
        PolicyCase(
            "allow-heredoc-body-with-semicolons",
            "python3 - <<'PY'\nimport os; print(os.getpid()); print(1)\nPY",
            True,
        ),
        # RTK read-only guard: native tool words inside quoted arguments or
        # heredoc bodies are not native invocations and must be allowed.
        PolicyCase(
            "allow-rtk-words-in-quoted-arg",
            'bd remember --key k "please find and grep the list"',
            True,
        ),
        PolicyCase(
            "allow-rtk-words-in-commit-message",
            'git commit -m "find and ls the files"',
            True,
        ),
        PolicyCase(
            "allow-rtk-words-in-heredoc-body",
            "python3 - <<'PY'\n# find grep ls git status in body\nprint('find grep ls')\nPY",
            True,
        ),
        PolicyCase(
            "deny-steam-applaunch-elden-ring",
            "steam -applaunch 1245620",
            False,
            "blocked this Elden Ring launch command",
        ),
        PolicyCase(
            "deny-steam-rungameid-elden-ring",
            "steam steam://rungameid/1245620",
            False,
            "blocked this Elden Ring launch command",
        ),
        PolicyCase(
            "deny-xdg-open-steam-run-elden-ring",
            "xdg-open steam://run/1245620",
            False,
            "blocked this Elden Ring launch command",
        ),
        # Manual pgrep is hard-blocked with no escape hatch (block_manual_pgrep).
        # On this WSL2 + Windows-Steam box pgrep false-NEGATIVES: Steam and the
        # game/EAC processes run as Windows processes visible only via
        # tasklist.exe, so `pgrep -x steam` reports "down" while it is up. Use
        # scripts/steam-running.sh for Steam / a WSL-aware check otherwise.
        # See bd steam-detection-wsl-false-negative-2026-07-18.
        PolicyCase(
            "deny-manual-pgrep-steam",
            "pgrep -x steam",
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-manual-pgrep-start-protected-detection",
            "pgrep -x start_protected_game.exe",
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-manual-pgrep-piped",
            "true | pgrep steam",
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-manual-pgrep-command-substitution",
            "echo $(pgrep -c steam)",
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-manual-pgrep-bash-c-quoted",
            "bash -c 'pgrep -x steam >/dev/null && echo up'",
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-runtime-preflight-pgrep-game-processes",
            "if pgrep -x eldenring.exe >/dev/null || pgrep -x start_protected_game.exe >/dev/null; then echo 'already running'; exit 2; fi",
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-python-subprocess-pgrep-quoted-arg",
            "python3 - <<'PY'\n"
            "import subprocess, os\n"
            "names=['eldenring.exe','start_protected_game.exe']\n"
            "for name in names:\n"
            "    p=subprocess.run(['pgrep','-x',name], text=True, capture_output=True)\n"
            "    print(name, p.returncode)\n"
            "PY",
            False,
            "manual pgrep is blocked",
        ),
        # The sanctioned WSL-aware Steam helper carries no pgrep command token in
        # the agent Bash string, so it is allowed (its internal pgrep lives inside
        # the script file, which is never an intercepted agent Bash command).
        PolicyCase(
            "allow-steam-running-helper",
            "bash scripts/steam-running.sh",
            True,
        ),
        # bd only records text: a single, non-chained bd invocation whose pgrep
        # token sits entirely inside quoted issue-tracker text is exempt
        # (2026-07-29 false positive, bd er-effects-rs-uxyz: a bd close --reason
        # describing launch-guard allow-tests was denied).
        PolicyCase(
            "allow-bd-close-reason-mentioning-pgrep",
            '$HOME/.local/bin/bd close er-effects-rs-aaa --reason "launch-guard'
            ' allow-test keeps pgrep -x start_protected_game.exe detection green"',
            True,
        ),
        # The original denied shape -- a chained bash -c batch of bd closes --
        # stays denied by design (not a single bd invocation).
        PolicyCase(
            "deny-chained-bash-c-bd-close-batch-mentioning-pgrep",
            "bash -c '\"$HOME/.local/bin/bd\" close er-effects-rs-aaa --reason"
            ' "keeps pgrep -x start_protected_game.exe detection green" &&'
            " \"$HOME/.local/bin/bd\" close er-effects-rs-bbb --reason \"second\"'",
            False,
            "manual pgrep is blocked",
        ),
        # ... and a bd text command chained with a real pgrep still denies.
        PolicyCase(
            "deny-bd-close-then-chained-pgrep",
            '$HOME/.local/bin/bd close er-effects-rs-aaa --reason "done" && pgrep -x steam',
            False,
            "manual pgrep is blocked",
        ),
        # git records the commit message, it never executes it. A commit whose
        # prose describes removing a raw process-name probe is documentation
        # (2026-08-12 false positive: `git commit -F - <<'EOF' ... EOF` was
        # denied for the message body, and the agent escape-hatched around the
        # guard by writing the message to a file). The live engine collapses
        # heredoc newlines to spaces, so these must run end-to-end here, not
        # only under `opa test`.
        #
        # Since 2026-10-17 a heredoc or substituted commit message can never be
        # authorized (the commit gate admits one plain `git commit` only), so the
        # heredoc cases below are denied by that gate, and assert instead that
        # the pgrep guard adds no denial of its own.
        PolicyCase(
            "commit-gate-denies-heredoc-message-mentioning-pgrep-pgrep-guard-quiet",
            "git commit -F - <<'EOF'\n"
            "preflight: stop probing the process table by name\n\n"
            "The preflight called pgrep -x steam directly, which false-negatives\n"
            "on this WSL2 + Windows-Steam box. It now sources\n"
            "scripts/steam-running.sh and calls steam_running instead.\n"
            "EOF",
            False,
            "does not commit without",
            absent_text="blocked a manual pgrep",
        ),
        PolicyCase(
            "allow-git-commit-dash-m-message-mentioning-pgrep",
            'git commit -m "preflight: drop the raw pgrep -x steam probe in'
            ' favour of scripts/steam-running.sh"',
            True,
        ),
        PolicyCase(
            "commit-gate-denies-cat-heredoc-substitution-mentioning-pgrep-pgrep-guard-quiet",
            "git add -A && git commit -m \"$(cat <<'EOF'\n"
            "guard: stop calling pgrep -x steam in the runtime preflight\n\n"
            "scripts/steam-running.sh is the sanctioned WSL-aware check.\n"
            'EOF\n)"',
            False,
            "does not commit without",
            absent_text="blocked a manual pgrep",
        ),
        # ... and the message carve-out must never launder an executing probe.
        PolicyCase(
            "deny-git-commit-heredoc-then-chained-pgrep",
            "git commit -F - <<'EOF'\n"
            "guard: drop the raw pgrep -x steam probe\n"
            "EOF\n"
            "pgrep -x steam",
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-git-commit-heredoc-unquoted-tag",
            "git commit -F - <<EOF\n"
            "guard: drop the raw pgrep -x steam probe\n"
            "EOF",
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-bash-c-wrapped-git-commit-heredoc-pgrep",
            "bash -c \"git commit -F - <<'EOF'\n"
            "guard: drop the raw pgrep -x steam probe\n"
            "EOF\n"
            '"',
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-git-commit-message-command-substitution-pgrep",
            'git commit -m "$(pgrep -x steam)"',
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-git-commit-dash-m-then-chained-pgrep",
            'git commit -m "guard: drop the raw process probe" && pgrep -x steam',
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "deny-git-commit-unquoted-pgrep-token",
            "git commit -am pgrep",
            False,
            "manual pgrep is blocked",
        ),
        # Word-boundary: a filename/word merely containing "pgrep" is not a pgrep
        # command token and must not be denied.
        PolicyCase(
            "allow-mypgreptool-word-not-pgrep",
            "./mypgreptool --version",
            True,
        ),
        # The pgrep guard stands down on native Linux, and only there (2026-09-26): pgrep sees
        # Steam and Proton directly on a native host. The listing that was denied on the native
        # dev box must pass there -- through both guards it tripped -- and still be refused on WSL
        # or on a host the signal cannot classify.
        PolicyCase(
            "allow-native-linux-pgrep-process-listing",
            REPORTED_PROCESS_LISTING,
            True,
            extra_env=(("CUPCAKE_HOST_PLATFORM_OVERRIDE", "native"),),
        ),
        PolicyCase(
            "deny-wsl-pgrep-process-listing",
            REPORTED_PROCESS_LISTING,
            False,
            "manual pgrep is blocked",
        ),
        PolicyCase(
            "allow-native-linux-pgrep-steam",
            "pgrep -x steam",
            True,
            extra_env=(("CUPCAKE_HOST_PLATFORM_OVERRIDE", "native"),),
        ),
        PolicyCase(
            "deny-unknown-host-pgrep-steam",
            "pgrep -x steam",
            False,
            "host_platform signal: unknown",
            extra_env=(("CUPCAKE_HOST_PLATFORM_OVERRIDE", "unknown"),),
        ),
        # `ER-EFFECTS-NO-GREP-FOR-BUILD-ERRORS` had no end-to-end case at all, and its rewrite into
        # token operations runs in the wasm runtime, where a green `opa test` proves nothing.
        PolicyCase(
            "deny-cargo-check-piped-into-grep",
            "timeout 28 cargo check -p er-quickload 2>&1 | grep -E 'error' -A6 | head -20; "
            'echo "--- clean ---"',
            False,
            "as its EXIT CODE",
        ),
        PolicyCase(
            "allow-cargo-tree-piped-into-grep",
            "cargo tree -i serde | grep serde",
            True,
        ),
        # 2026-10-16: a quoted-tag heredoc body is literal, so a message quoting a build piped
        # into grep inside a substitution builds nothing. The engine delivers the body with its
        # newlines turned into spaces, which is the shape these exercise; the `; ` after the
        # terminator is the one scripts/cupcake-hook.sh writes.
        PolicyCase(
            "allow-heredoc-message-quoting-a-substitution-build",
            "cat > target/msg.txt <<'EOF'\nfix: quote `echo $(cargo build 2>&1) | grep error` in docs\nEOF",
            True,
        ),
        PolicyCase(
            "deny-build-grep-after-a-substitution-heredoc-body",
            "cat > target/msg.txt <<'EOF'\nfix: quote `echo $(date)` in docs\nEOF; "
            "cargo build -p er-quickload 2>&1 | grep error",
            False,
            "as its EXIT CODE",
        ),
        PolicyCase(
            "deny-push-after-a-backtick-heredoc-body",
            "cat > target/msg.txt <<'EOF'\nfix: quote `echo $(date)` in docs\nEOF; git push origin main",
            False,
            "The agent does not push",
        ),
        PolicyCase(
            "deny-push-after-a-backtick-heredoc-body-without-the-shim",
            "cat > target/msg.txt <<'EOF'\nfix: quote `echo $(date)` in docs\nEOF\ngit push origin main",
            False,
            "The agent does not push",
        ),
        PolicyCase(
            "deny-push-on-a-heredoc-opener-line",
            "cat > target/msg.txt <<'EOF' ; git push origin main\nfix: quote `x` in docs\nEOF",
            False,
            "The agent does not push",
        ),
        PolicyCase(
            "deny-pgrep-then-proton-start-protected-launch",
            "pgrep -x start_protected_game.exe >/dev/null; proton run /tmp/start_protected_game.exe",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-proton-start-protected-game",
            "proton run /tmp/start_protected_game.exe",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-wine-start-protected-game",
            "wine /opt/er/start_protected_game.exe",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-dot-slash-start-protected-game",
            "./start_protected_game.exe",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        # 2026-08-04 false positive: Naming the EAC launcher as data was denied.
        # The blocked command recorded a memory about the launcher (that
        # /proc/<pid>/comm truncates at 15 chars, so an exact-match entry for
        # the 24-char name could never match a process). The substring fallback
        # asked only "payload contains the name" plus a generic marker word,
        # and "bash" in prose is such a word; the bd text exemption could not
        # rescue it because its shape did not anticipate a quoted binary path.
        # Deny now needs the name to occur somewhere it could be executed.
        PolicyCase(
            "allow-bd-remember-quoted-binary-path-naming-eac-launcher",
            '"$HOME/.local/bin/bd" remember "er-stale-run-sentinel compared'
            " start_protected_game.exe (24 chars) verbatim against"
            " /proc/<pid>/comm, which the kernel truncates at 15 chars, so that"
            " entry could never match any process; run bash"
            ' scripts/er-stale-run-sentinel.sh --selftest" --key'
            " stale-sentinel-comm-truncation-2026-08-04",
            True,
        ),
        PolicyCase(
            "allow-echo-prose-naming-eac-launcher",
            "echo 'the sentinel detects start_protected_game.exe; never launch"
            " it from bash or proton wrappers'",
            True,
        ),
        PolicyCase(
            "allow-python-c-string-literal-naming-eac-launcher",
            "python3 -c \"print('sentinel comm entry:"
            " start_protected_game.exe truncated to 15 chars')\"",
            True,
        ),
        # ... and naming-as-data must not become a launch bypass: an inert first
        # statement does not launder a launch in the second, and an inert head
        # does not make a pipe inert.
        PolicyCase(
            "deny-echo-prose-then-setsid-bare-launcher",
            "echo 'do not run start_protected_game.exe from bash'; setsid"
            " start_protected_game.exe",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-echo-launcher-name-piped-into-shell",
            "echo 'start_protected_game.exe' | bash",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        # Read-only /proc comm scans may name the EAC launcher inside quoted
        # string literals (2026-07-05 false positive: the sanctioned no-pgrep
        # process-detection heredoc was denied by the raw marker fallback).
        PolicyCase(
            "allow-proc-comm-scan-heredoc-naming-eac-launcher",
            "python3 - <<'PY'\n"
            "import glob\n"
            "names = ('steam', 'eldenring.exe', 'start_protected_game.exe')\n"
            "found = {n: False for n in names}\n"
            "for path in glob.glob('/proc/[0-9]*/comm'):\n"
            "    try:\n"
            "        comm = open(path).read().strip()\n"
            "    except OSError:\n"
            "        continue\n"
            "    if comm in names:\n"
            "        found[comm] = True\n"
            "for n in names:\n"
            "    print(n, 'up' if found[n] else 'down')\n"
            "PY",
            True,
            extra_tool_input={
                "description": "Report Steam/eldenring/EAC launcher process state from /proc"
            },
        ),
        PolicyCase(
            "allow-proc-comm-scan-python-c-naming-eac-launcher",
            "python3 -c 'import glob; print(any(open(p).read().strip() =="
            ' "start_protected_game.exe" for p in glob.glob("/proc/[0-9]*/comm")))\'',
            True,
        ),
        # Exact /proc cleanup/teardown for stale Elden Ring/EAC launcher
        # processes is allowed: it names the protected launcher only as data,
        # reads /proc, and sends signals to exact matching pids without
        # launching the named executable (er-effects-rs-9iz).
        PolicyCase(
            "allow-proc-comm-scan-sigterm-sigkill-cleanup-naming-eac-launcher",
            "python3 - <<'PY'\n"
            "import glob, os, signal\n"
            "names = {'eldenring.exe', 'start_protected_game.exe'}\n"
            "for path in glob.glob('/proc/[0-9]*/comm'):\n"
            "    try:\n"
            "        pid = int(path.split('/')[2])\n"
            "        comm = open(path).read().strip()\n"
            "    except (OSError, ValueError):\n"
            "        continue\n"
            "    if comm in names:\n"
            "        for sig in (signal.SIGTERM, signal.SIGKILL):\n"
            "            try:\n"
            "                os.kill(pid, sig)\n"
            "            except OSError:\n"
            "                pass\n"
            "PY",
            True,
        ),
        # Editing a repo file whose text names the EAC launcher is not a launch
        # (2026-08-04 false positive: removing one sentence from a module
        # docstring was denied because the raw marker fallback saw the name plus
        # the word "python" from the interpreter's own invocation). This repo
        # deliberately writes refusal logic and safety docs that name the
        # forbidden binary, so that text has to stay editable.
        # Written as a shell heredoc into a file rather than as a python program.
        #
        # It used to be `python3 - <<'PY' ... p.write_text(...)`, and that stopped being a
        # legitimate shape on 2026-09-16 when `no_python_file_write` began refusing inline python
        # that writes files -- so this case asserted `allow` against a command the rulebook now
        # denies for a reason that has nothing to do with what it is testing, and
        # `stage / policy` went red. The subject here was never python: it is whether a command
        # naming the forbidden binary inside ordinary prose is mistaken for an attempt to
        # launch it. A heredoc into a file is what the python guard itself points at as the
        # visible-in-the-command way to write one, so the same text reaches the launch guard with
        # nothing else for any other policy to object to.
        PolicyCase(
            "allow-heredoc-writing-docstring-naming-eac-launcher",
            "cat > scripts/frida-dump-module.py <<'EOF'\n"
            '"""Dump a module out of the running game.\n'
            "\n"
            "* Offline `eldenring.exe` ONLY. Refuses `start_protected_game.exe` / EAC, like the\n"
            "  sibling `frida-nudge.py`.\n"
            '"""\n'
            "EOF",
            True,
            extra_tool_input={"description": "Rewrite the docstring that names the EAC launcher"},
        ),
        # ... but the exemption must not become a launch bypass. A pipe on the
        # heredoc REDIRECTION line feeds the program's output to a shell, so a
        # path the program merely prints really executes.
        PolicyCase(
            "deny-python-heredoc-edit-piped-into-shell",
            "python3 - <<'PY' | bash\n"
            "print(\"'/opt/er/start_protected_game.exe'\")\n"
            "PY",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-python-heredoc-edit-plus-subprocess-launch",
            "python3 - <<'PY'\n"
            "from pathlib import Path\n"
            "import subprocess\n"
            "Path('scripts/frida-dump-module.py').write_text('# start_protected_game.exe')\n"
            "subprocess.run(['wine', '/opt/er/start_protected_game.exe'])\n"
            "PY",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        # ... but the /proc mention must never become a launch bypass.
        PolicyCase(
            "deny-proc-scan-heredoc-with-subprocess-launch",
            "python3 - <<'PY'\n"
            "import subprocess\n"
            "print(open('/proc/1/comm').read())\n"
            "subprocess.run(['wine', 'start_protected_game.exe'])\n"
            "PY",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-proc-scan-heredoc-trailing-quoted-launch",
            "python3 - <<'PY'\n"
            "print(open('/proc/1/comm').read())\n"
            "PY\n"
            "setsid '/opt/er/start_protected_game.exe'",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-direct-start-protected-game",
            "/tmp/start_protected_game.exe",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-ersc-dll-copy-bundle",
            "cp -f /tmp/ersc.dll target/release/ersc.dll",
            False,
            "blocked this Seamless Co-op DLL bundling command",
        ),
        # git commit message text may mention ersc.dll (2026-07-07 false
        # positive: a quoted -m body mentioning ersc.dll plus prose marker
        # substrings like "stage"/"target" was denied by the raw fallback).
        # The `git add -A &&` and heredoc forms can no longer be authorized
        # (2026-10-17 commit gate), so the first is the plain commit it would be
        # today and the second asserts the bundling guard stays quiet beside the
        # commit gate's denial.
        PolicyCase(
            "allow-git-commit-message-mentioning-ersc-dll",
            'git commit -am "loader precedence: a resident'
            ' ersc.dll wins over the env hint; never stage it into target/"',
            True,
        ),
        PolicyCase(
            "commit-gate-denies-heredoc-message-mentioning-ersc-dll-bundling-guard-quiet",
            'git add -A && git commit -m "$(cat <<\'EOF\'\n'
            "guard: document that a resident ersc.dll wins over the env hint\n\n"
            "The bundling rule still blocks staging ersc.dll into release artifacts.\n"
            'EOF\n)"',
            False,
            "does not commit without",
            absent_text="Seamless Co-op DLL bundling",
        ),
        PolicyCase(
            "deny-cp-seamless-ersc-dll-to-dist",
            "cp SeamlessCoop/ersc.dll dist/",
            False,
            "blocked this Seamless Co-op DLL bundling command",
        ),
        PolicyCase(
            "deny-git-commit-chained-quoted-ersc-copy",
            "git commit -m \"note\" && cp 'SeamlessCoop/ersc.dll' dist/",
            False,
            "blocked this Seamless Co-op DLL bundling command",
        ),
        # 2026-07-28 false positive: the bundling rule denied a `bd remember`
        # whose quoted memory body merely mentions ersc.dll while documenting
        # Seamless compatibility. Two defects: the bd text exemption matched only
        # a hard-coded /home/banon path (not the `$HOME/.local/bin/bd` form
        # AGENTS.md documents), and the fallback fired on "ersc.dll" plus any of
        # the substrings stage/bundle/archive/tar/rar -- which hide inside
        # "target"/"startup"/"library"/"staged". Deny now needs a real verb.
        PolicyCase(
            "allow-bd-remember-home-var-mentioning-ersc-dll",
            "$HOME/.local/bin/bd remember \"SAVE-DISABLE INTERCEPTION STRATEGY:"
            " swallow the SL submit and fake the status poll. Also above any path"
            " redirection so it works identically under Seamless Co-op (ersc.dll"
            " redirects paths, not the SL submit). Nothing is staged into the"
            ' target/ bundle." --key'
            " save-disable-strategy-swallow-SL-submit-fake-status-poll-2026-07-28",
            True,
        ),
        PolicyCase(
            "allow-git-commit-message-mentioning-ersc-dll-seamless-compat",
            'git commit -m "docs: record that ersc.dll redirects save paths under'
            ' Seamless Co-op; nothing is staged into target/"',
            True,
        ),
        PolicyCase(
            "allow-echo-prose-mentioning-ersc-dll",
            "echo 'ersc.dll is a compatibility target; it is never archived into"
            " the target/ bundle'",
            True,
        ),
        # ... while real staging/packaging stays denied.
        PolicyCase(
            "deny-cp-ersc-dll-into-me3-profile",
            'cp -f "/mnt/c/SteamLibrary/steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll"'
            " /home/banon/Elden/profile/ersc.dll",
            False,
            "blocked this Seamless Co-op DLL bundling command",
        ),
        PolicyCase(
            "deny-mv-quoted-ersc-dll-into-target-bundle",
            "mv 'SeamlessCoop/ersc.dll' target/release-bundle/",
            False,
            "blocked this Seamless Co-op DLL bundling command",
        ),
        PolicyCase(
            "deny-redirect-write-target-ersc-dll",
            "cat vendor/seamless-coop-v1.9.9/SeamlessCoop/ersc.dll > target/release/ersc.dll",
            False,
            "blocked this Seamless Co-op DLL bundling command",
        ),
        # Restoring the user's game-installed co-op DLL (same-path rename that
        # only strips the repo's .er-quickload-staged suffix) is the opposite of
        # bundling and is allowed (bd er-effects-rs-gkqa).
        PolicyCase(
            "allow-mv-restore-staged-ersc-dll-same-gameinstall-path",
            "mv -f '/mnt/c/SteamLibrary/steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll.er-quickload-staged'"
            " '/mnt/c/SteamLibrary/steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll'",
            True,
        ),
        # ... but the same staged source moved to any other destination denies.
        PolicyCase(
            "deny-mv-staged-ersc-dll-into-target-bundle",
            "mv -f '/mnt/c/SteamLibrary/steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll.er-quickload-staged'"
            " 'target/release-bundle/ersc.dll'",
            False,
            "blocked this Seamless Co-op DLL bundling command",
        ),
        # A read-only interpreter scan of the installed DLL with the path as an
        # unquoted operand is allowed (bd er-effects-rs-gkqa: arm (a) counted
        # python/bash as bundling verbs).
        PolicyCase(
            "allow-python-scan-gameinstall-ersc-dll-unquoted-operand",
            "python3 scripts/pe_export_dump.py"
            " /mnt/c/SteamLibrary/steamapps/common/ELDEN\\ RING/Game/SeamlessCoop/ersc.dll",
            True,
        ),
        # ... while a repo-relative operand (the staging-script shape) denies.
        PolicyCase(
            "deny-python-script-repo-relative-ersc-dll-operand",
            "python3 scripts/pe_export_dump.py SeamlessCoop/ersc.dll",
            False,
            "blocked this Seamless Co-op DLL bundling command",
        ),
        # False positive fixed 2026-08-15 (arm (a')): neither the copy/archive
        # verb list (a) nor the interpreter word list (a') required a word
        # boundary after the matched token, so `[^;|&()]*` let the match start
        # partway through an unrelated word whose prefix happened to equal one
        # of those tokens. `sha256sum` starts with `sh` (an (a') interpreter
        # token), so a read-only hash compare of two files -- one path staged
        # inside quotes, the other an unquoted game-install operand, piped
        # through `sed` to redact the home directory -- denied as if it were a
        # bundling command. It copies, moves, and writes nothing.
        PolicyCase(
            "allow-sha256sum-compare-staged-and-gameinstall-ersc-dll",
            'G="/home/banon/.local/share/Steam/steamapps/common/ELDEN RING/Game/SeamlessCoop"\n'
            'sha256sum "$G/ersc.dll.er-quickload-staged" /home/banon/Elden/ersc.dll'
            " | sed 's|/home/banon|~|'",
            True,
        ),
        # A bare read-only stat/listing of an ersc.dll path (no copy/archive
        # verb, no interpreter, no redirect) must always allow.
        PolicyCase(
            "allow-stat-gameinstall-ersc-dll",
            'stat "/home/banon/.local/share/Steam/steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll"',
            True,
        ),
        PolicyCase(
            "allow-ls-la-gameinstall-ersc-dll",
            "ls -la /home/banon/Elden/ersc.dll",
            True,
        ),
        # Chaining a second command onto the restore-rename deliberately
        # forfeits the same-path user-restore exemption -- the exemption's
        # fail-closed shape requires the whole command to be a single `mv`
        # with exactly two quoted operands, so anything appended (even a
        # read-only `ls -la`) drops back to the plain file-moving arms, which
        # deny with no destination scoping. This is intentional and must not
        # regress: an exempted restore command is not a place to smuggle a
        # second statement.
        PolicyCase(
            "deny-mv-restore-staged-ersc-dll-chained-with-ls",
            "mv -f '/mnt/c/SteamLibrary/steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll.er-quickload-staged'"
            " '/mnt/c/SteamLibrary/steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll'"
            " && ls -la '/mnt/c/SteamLibrary/steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll'",
            False,
            "blocked this Seamless Co-op DLL bundling command",
        ),
        PolicyCase(
            "allow-quoted-forbidden-launch-note",
            "echo 'do not run steam -applaunch 1245620'",
            True,
        ),
        # bd only records text; forbidden-form mentions in quoted issue text
        # must not deny even when generic executable marker words ("bash",
        # "python", ...) appear in the prose (2026-07-04 false positive).
        PolicyCase(
            "allow-bd-create-mentioning-eac-launcher",
            '/home/banon/.local/bin/bd create "me3 launch path" -d "me3 Linux'
            " launch via bash scripts must not use forbidden forms (steam"
            ' -applaunch / steam:// URLs / start_protected_game.exe)." -t task -p 1',
            True,
        ),
        PolicyCase(
            "allow-bd-remember-mentioning-eac-launcher",
            "/home/banon/.local/bin/bd remember --key k 'never launch"
            " start_protected_game.exe from bash or python wrappers'",
            True,
        ),
        PolicyCase(
            "allow-bd-create-mentioning-steam-applaunch-appid",
            '/home/banon/.local/bin/bd create "launch policy" -d "steam'
            ' -applaunch 1245620 is a forbidden form; drive it from bash probes" -t task',
            True,
        ),
        # The bd exemption must not leak to chained or indirected launches.
        PolicyCase(
            "deny-bd-chained-proton-start-protected",
            '/home/banon/.local/bin/bd create "note" -d "text" && proton run'
            " /tmp/start_protected_game.exe",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-bd-chained-python-c-start-protected",
            '/home/banon/.local/bin/bd create "note" -d "text"; python3 -c'
            " 'import subprocess; subprocess.run([\"proton\",\"run\",\"start_protected_game.exe\"])'",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-bash-c-start-protected-game",
            "bash -c '/opt/er/start_protected_game.exe'",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-ctx-execute-python-steam-applaunch",
            "",
            False,
            "blocked this Elden Ring launch command",
            {"language": "python", "code": "import subprocess; subprocess.run(['steam','-applaunch','1245620'])"},
            tool_name="ctx_execute",
        ),
        PolicyCase(
            "deny-heredoc-python-start-protected",
            "python3 - <<'PY'\nimport subprocess\nsubprocess.run(['proton','run','start_protected_game.exe'])\nPY",
            False,
            "blocked this Elden Ring EAC launcher command",
        ),
        PolicyCase(
            "deny-ctx-execute-python-ersc-copy",
            "",
            False,
            "blocked this Seamless Co-op DLL bundling command",
            {"language": "python", "code": "import shutil; shutil.copy2('SeamlessCoop/ersc.dll', 'target/release/ersc.dll')"},
            tool_name="ctx_execute",
        ),
        PolicyCase(
            "allow-mutating-git-branch-delete",
            "git branch -d merged-topic",
            True,
        ),
        # A real rtk invocation with a native word in a quoted arg stays allowed.
        PolicyCase(
            "allow-rtk-grep-quoted-find",
            'rtk grep "find"',
            True,
        ),
        # No authoring scripts into /tmp (artifacts to /tmp are fine).
        PolicyCase(
            "deny-write-script-into-tmp",
            "",
            False,
            "authoring a script into /tmp",
            {"file_path": "/tmp/ghidra_scripts/Foo.java", "content": "class Foo {}"},
            include_timeout=False,
            tool_name="Write",
        ),
        PolicyCase(
            "deny-edit-py-script-into-tmp",
            "",
            False,
            "authoring a script into /tmp",
            {"file_path": "/tmp/scratch/tool.py"},
            include_timeout=False,
            tool_name="Edit",
        ),
        PolicyCase(
            "allow-write-data-artifact-into-tmp",
            "",
            True,
            None,
            {"file_path": "/tmp/claude/dump_funcs.tsv", "content": "a\tb"},
            include_timeout=False,
            tool_name="Write",
        ),
        PolicyCase(
            "allow-write-script-into-repo",
            "",
            True,
            None,
            {"file_path": str(REPO_ROOT / "scripts" / "ghidra" / "Foo.java"), "content": "class Foo {}"},
            include_timeout=False,
            tool_name="Write",
        ),
        # Extracting a committed file is not authoring it: the measured 2026-10-22 refusal,
        # admitted through the whole rulebook, and the same extraction with a hand-written line
        # appended still refused. The append is `echo`, not a `python3` run, because
        # `bash_no_python_file_write` answers a python run first and this guard's reason never
        # reaches the output.
        PolicyCase(
            "allow-git-show-rev-path-into-scratchpad",
            "git show main:.cupcake/tests/teardown_must_relaunch_test.rego > "
            "/tmp/claude-1000/-home-banon-projects-er-mods-rs/session/scratchpad/old_test.rego",
            True,
        ),
        PolicyCase(
            "deny-git-show-into-tmp-then-append",
            "git show main:scripts/probe.sh > /tmp/probe.sh && echo 'exit 0' >> /tmp/probe.sh",
            False,
            "authoring a script into /tmp",
        ),
        # `ER-EFFECTS-TRANSCRIPT-WRITE-GUARD`, live: the push authorization is read from the session
        # transcript, so the agent may read transcripts and never rewrite one.
        PolicyCase(
            "deny-write-tool-on-session-transcript",
            "",
            False,
            "Session transcripts under ~/.claude/projects",
            {"file_path": "/home/u/.claude/projects/-home-u-repo/abc.jsonl", "content": "{}"},
            include_timeout=False,
            tool_name="Write",
        ),
        PolicyCase(
            "deny-redirect-into-session-transcript",
            "echo '{}' >> /home/u/.claude/projects/-home-u-repo/abc.jsonl",
            False,
            "Session transcripts under ~/.claude/projects",
        ),
        PolicyCase(
            "allow-reading-session-transcript",
            "jq -c . /home/u/.claude/projects/-home-u-repo/abc.jsonl 2>/dev/null",
            True,
        ),
        PolicyCase(
            "allow-write-tool-on-auto-memory",
            "",
            True,
            None,
            {"file_path": "/home/u/.claude/projects/-home-u-repo/memory/MEMORY.md", "content": "x"},
            include_timeout=False,
            tool_name="Write",
        ),
        PolicyCase(
            "allow-write-log-into-tmp",
            "",
            True,
            None,
            {"file_path": "/tmp/run.log", "content": "ok"},
            include_timeout=False,
            tool_name="Write",
        ),
        # `ER-EFFECTS-COMMENT-CAPS-GUARD`, live. Its opa suite proves the logic; these two prove the
        # policy survives cupcake's WASM runtime, which is the half that went inert for 36 days
        # once already. The allow case is the load-bearing one: this guard refuses a write, so a
        # false positive costs an author an edit they cannot make.
        # Aimed at a `.py` file, not at Rust under `crates/`.
        #
        # The caps guard scans `.rs`, `.py`, `.sh` and `.bash` alike, so the subject is unchanged --
        # but `no_rust_edit_without_frida_proof` denies every `.rs` under `crates/` whenever the
        # last Frida measurement has been spent by a commit, and then that refusal is the one
        # cupcake returns. The expected text goes missing and this case fails for a reason that has
        # nothing to do with capitals. It also fails asymmetrically: after a local Frida run it
        # passes, and in continuous integration -- which never runs Frida and so never holds a
        # measurement -- it can never pass. A policy test must not depend on which of two guards
        # fires first, and a file the other guard does not look at is how this one stops depending
        # on it.
        PolicyCase(
            "deny-write-shouted-word-into-a-comment",
            "",
            False,
            "shouted word going into a comment",
            {
                "file_path": str(REPO_ROOT / "scripts" / "probe-example.py"),
                "content": "# this is NOT the same pointer\ndef f():\n    pass\n",
            },
            include_timeout=False,
            tool_name="Write",
        ),
        PolicyCase(
            "allow-write-backticked-mnemonic-in-a-comment",
            "",
            True,
            None,
            {
                # A `.py` file for the same reason as the deny case above: an `allow` expectation
                # against Rust under `crates/` is really an expectation that no second guard denies
                # it either, and `no_rust_edit_without_frida_proof` always does once a commit has
                # spent the measurement. This case is the load-bearing one of the pair -- it proves
                # backticks rescue a mnemonic -- so it must fail only when backticks stop working.
                "file_path": str(REPO_ROOT / "scripts" / "probe-example.py"),
                "content": "# the x86 `NOT` instruction, quoted\ndef f():\n    pass\n",
            },
            include_timeout=False,
            tool_name="Write",
        ),
        # AskUserQuestion (the multiple-choice questionnaire tool). Corrected 2026-08-15: the prior
        # unconditional PreToolUse deny (block_askuserquestion) fired outside /goal work -- a legitimate
        # design-interview question from the `grilling` skill was blocked while not in any /goal work.
        # User verdict: "That cupcake policy is not triggered correctly." Re-investigation found no
        # reliable goal-active signal to gate a conditional deny on, and PreToolUse cannot carry a
        # non-blocking advisory in this build either (empirically confirmed: add_context/ask both no-op
        # to Allow on PreToolUse), so the deny is removed outright. AskUserQuestion now proceeds
        # unconditionally through this file; the advisory reminder lives in the companion
        # block_askuserquestion_reminder.rego (UserPromptSubmit/add_context, exercised via opa test, not
        # this live-engine harness).
        PolicyCase(
            "allow-askuserquestion-questionnaire",
            "",
            True,
            None,
            {"questions": [{"question": "Which?", "header": "H", "options": [{"label": "A"}, {"label": "B"}]}]},
            include_timeout=False,
            tool_name="AskUserQuestion",
        ),
    ]

    # --- ER launcher-name non-execution cases ------------------------------------------------------
    # A launcher script path passed as an argument to a non-executing command must remain allowed.
    # The separate forbidden-form launch guard still blocks Steam/EAC/start_protected_game/ersc.dll
    # paths; the removed per-prompt launch-clearance gate is intentionally no longer tested here.
    cases.extend([
        PolicyCase("allow-git-add-launcher-name", "git add scripts/run-vanilla-reload-agentdriven.sh", True),
        PolicyCase("allow-shellcheck-launcher-name", "shellcheck scripts/run-camera-smoke.sh", True),
    ])

    # --- `ER-EFFECTS-REQUIRE-RUNTIME-EVIDENCE`, through the live engine -----------------------------
    # The verdict is pinned through the signal's own override, the way the branch guards pin
    # `current_branch`, so these say nothing about what has been built or launched on this machine.
    # What they assert is the wiring: that the word denies, that the sentence survives the trip into
    # the refusal, and that a redirected push is judged on the same word rather than on a special
    # case of its own. The signal-side half -- which repository that word is about -- is asserted in
    # run_runtime_evidence_signal_checks() above and in
    # `python3 scripts/cupcake_push_target_repo.py --selftest`.
    evidence_note = "the newest log er-quickload-autoload-debug.log (er_quickload.dll) was built from 5dd9b032, not 92d621cd"
    missing_env = (
        ("CUPCAKE_RUNTIME_EVIDENCE_OVERRIDE", "MISSING"),
        ("CUPCAKE_RUNTIME_EVIDENCE_NOTE_OVERRIDE", evidence_note),
    )
    cases.extend([
        PolicyCase(
            "deny-push-of-game-code-that-has-never-run",
            "git push -u origin feat/x",
            False,
            evidence_note,
            extra_env=missing_env,
        ),
        # The false negative this guard was blind to until 2026-09-13: the push runs somewhere else,
        # and the verdict is about that somewhere else.
        PolicyCase(
            "deny-push-redirected-to-a-checkout-with-no-evidence",
            "cd /other/worktree && git push -u origin HEAD",
            False,
            evidence_note,
            extra_env=missing_env,
        ),
        # `UNKNOWN` is not `MISSING`, and a redirect the signal cannot resolve produces it.
        PolicyCase(
            "any-push-guard-denies-push-whose-target-the-signal-could-not-resolve",
            "(cd /other/worktree && git push)",
            False,
            "The agent does not push",
            extra_env=(("CUPCAKE_RUNTIME_EVIDENCE_OVERRIDE", "UNKNOWN"),),
        ),
        PolicyCase(
            "any-push-guard-denies-push-when-no-crate-changed-in-the-pushed-checkout",
            "cd /other/worktree && git push -u origin HEAD",
            False,
            "The agent does not push",
            extra_env=(("CUPCAKE_RUNTIME_EVIDENCE_OVERRIDE", "NOTRUNTIME"),),
        ),
        # `no_whole_check_sh`. Its own `opa test` suite is 21 green assertions, and the header of
        # this file already says why that is not enough: `opa test` links RE2 natively while the
        # engine that ships evaluates policies as wasm, and one rule here was green in the
        # interpreter while crashing every policy in production. These two are the ends of the
        # range -- the command denied, and the same words allowed when a commit message merely
        # names them. The second is the one that has to keep passing: `require_scoped_cargo`
        # records a guard that blocked the edit removing it, and a guard on this script that
        # cannot be described in the commit that removes it is unwritable in the repo it guards.
        PolicyCase(
            "check-sh-guard-denies-a-stage-run",
            "bash scripts/check.sh --stage lint",
            False,
            "The agent does not run scripts/check.sh",
        ),
        PolicyCase(
            "allow-commit-message-naming-the-check-script",
            'git commit -m "guard: the agent no longer runs bash scripts/check.sh"',
            True,
        ),
        # `me3_launch_needs_windows_binaries_dir`, live for the reason above: its opa suite runs in
        # the interpreter, and the rulebook runs it as wasm. The first case is the launch that hung
        # on 2026-10-22 (me3 took the repo's target/ dir for its Windows binaries); the second is
        # the form the deny message offers; the third names the first in a grep pattern.
        PolicyCase(
            "me3-guard-denies-a-launch-without-windows-binaries-dir",
            "cd /home/banon/projects/er-mods-rs && /home/banon/.local/bin/me3 launch --auto-detect -p /home/banon/Elden/quicksave.me3",
            False,
            "does not name its Windows binaries",
        ),
        PolicyCase(
            "me3-guard-allows-a-launch-naming-windows-binaries-dir",
            "/home/banon/.local/bin/me3 --windows-binaries-dir /home/banon/.local/share/me3/windows-bin launch --auto-detect -p /home/banon/Elden/quicksave.me3",
            True,
        ),
        PolicyCase(
            "me3-guard-allows-a-grep-naming-the-launch",
            'grep -rn "me3 launch" scripts/',
            True,
        ),
        # `cap_parallel_fanout`, live for the same reason: its reading of a quoted `bash -c`
        # payload inside a `systemd-run` scope goes through the shared decomposition, which is
        # where the wasm runtime and `opa test` have disagreed before. The first case is the
        # fan-out measured 2026-10-15 before systemd-oomd killed the kitty terminal scope; the
        # second is the capped form the deny message offers; the third names the first in prose.
        PolicyCase(
            "fanout-guard-denies-the-measured-xargs-run",
            "ls work/*/orig.ll | xargs -n 200 -P 8 python3 $P/pos_slice.py",
            False,
            "fan-out of more than 4 parallel workers",
        ),
        PolicyCase(
            "fanout-guard-allows-a-memory-capped-scope",
            'systemd-run --user --scope -p MemoryMax=8G -- bash -c "ls work/*/orig.ll | xargs -n 200 -P 12 python3 x.py"',
            True,
        ),
        PolicyCase(
            "allow-commit-message-naming-the-fanout",
            'git commit -m "guard: ls work/*/orig.ll | xargs -P 8 python3 pos_slice.py took the terminal down"',
            True,
        ),
    ])

    # --- payload versus command (bd er-effects-rs-ak3q, bd er-effects-rs-if5l) -------------
    #
    # Three guards matched the whole command string, so any tool taking prose that quotes
    # another command's name tripped them. All three blocks below happened in production on
    # 2026-09-21, and the cost was not the block: a commit message had to be reworded to say
    # "a repo-relative teardown command" instead of naming the file, and the issue reporting
    # it had to be filed through a body file written by a separate command in order to exist.
    # Driven through the real engine because `opa test` and the live shim disagree about
    # whitespace, and the shim is where the quoted-span reading actually happens.
    cases.extend([
        PolicyCase(
            "allow-commit-message-naming-the-teardown-script",
            'git commit -m "fix(guard): teardown_must_relaunch fires on scripts/er-teardown.py in prose"',
            True,
        ),
        PolicyCase(
            "allow-issue-body-naming-the-teardown-script",
            'bd create --description "scripts/er-teardown.py must be paired with a launch"',
            True,
        ),
        PolicyCase(
            "allow-memory-body-quoting-a-python-script-run",
            'bd remember --key trace-tsv "measured with python3 scripts/er-fd-trace.py --tsv ... 12 rows"',
            True,
        ),
        PolicyCase(
            "allow-committed-script-with-a-dotdot-operand",
            "python3 scripts/er-frida-evidence.py --check --out ../trace.tsv",
            True,
        ),
        # ...and every one of those guards still reaches a real invocation.
        PolicyCase(
            "deny-teardown-chained-with-other-work",
            "python3 scripts/er-teardown.py; cargo build -p er-quickload",
            False,
            "teardown that does not relaunch",
        ),
        # Being the whole command stopped being enough. This case used to assert the opposite --
        # `allow-teardown-that-is-the-whole-command`, no signal, allow -- and that expectation is
        # the stale half: `teardown_must_relaunch` now excuses a standalone teardown only after a
        # measurement, so the same string the old case allowed is what the policy's own
        # `test_deny_teardown_alone_with_a_reason_without_evidence` requires it to deny. Both
        # directions are asserted here because only the pair can tell "the exception works" apart
        # from "the exception never fires".
        PolicyCase(
            "deny-teardown-alone-without-measurement",
            "python3 scripts/er-teardown.py --reason policy-regression",
            False,
            "teardown that does not relaunch",
        ),
        PolicyCase(
            "allow-teardown-alone-after-measurement",
            "python3 scripts/er-teardown.py --reason policy-regression",
            True,
            extra_env=(("ER_FRIDA_EVIDENCE_LOG", str(FRIDA_EVIDENCE_PROVEN)),),
        ),
        # A run that has become invalid has produced no measurement, and AGENTS.md obliges the
        # agent to stop it, so the declared form passes with the verdict pinned absent. The
        # second case is the command refused on 2026-10-07 with the declaration added; the
        # deny cases keep chained work and a multi-word reason refused.
        PolicyCase(
            "allow-invalid-run-teardown-without-measurement",
            "python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=invalid-run-build-import-did-not-apply",
            True,
        ),
        PolicyCase(
            "allow-invalid-run-teardown-with-output-trim",
            "python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py"
            " --reason=invalid-run-build-import-did-not-apply 2>&1 | tail -3",
            True,
        ),
        PolicyCase(
            "deny-invalid-run-teardown-then-build",
            "python3 scripts/er-teardown.py --reason=invalid-run-wrong-build; cargo build",
            False,
            "teardown that does not relaunch",
        ),
        PolicyCase(
            "deny-invalid-run-teardown-multi-word-reason",
            'python3 scripts/er-teardown.py --reason="invalid-run-import did not apply"',
            False,
            "teardown that does not relaunch",
        ),
        # argparse prints usage and exits before any teardown code runs, like `--status`.
        PolicyCase(
            "allow-teardown-help",
            "python3 scripts/er-teardown.py --help 2>&1 | head -15",
            True,
        ),
        PolicyCase(
            "allow-teardown-short-help",
            "python3 scripts/er-teardown.py -h",
            True,
        ),
        PolicyCase(
            "deny-teardown-help-then-a-real-teardown",
            "python3 scripts/er-teardown.py --help; python3 scripts/er-teardown.py; cargo build",
            False,
            "teardown that does not relaunch",
        ),
        # The command refused on 2026-10-15. The deny came from `.worktrees/er-sky/.cupcake`,
        # which the global hook evaluates when the session's shell sits in that worktree (it
        # passes no `--policy-dir`), and that branch lacks the name test for `NAME=/path`. This
        # tree's rulebook allows it; run through the compiled engine because that test is
        # `indexof`/`substring`/`split` arithmetic the WASM build has to agree with.
        PolicyCase(
            "allow-teardown-then-scratchpad-profile-user-launcher",
            "python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py > /dev/null 2>&1;"
            " ME3_PROFILE=/tmp/claude-1000/-home-banon-projects-er-mods-rs/"
            "e7edaee5-d1da-4868-ab95-fa3613643f52/scratchpad/er-sky-sun.me3"
            " /home/banon/Elden/launch.sh -o"
            " > /tmp/claude-1000/-home-banon-projects-er-mods-rs/"
            "e7edaee5-d1da-4868-ab95-fa3613643f52/scratchpad/er-sky-sun-launch.log 2>&1",
            True,
        ),
        PolicyCase(
            "deny-inline-python-file-write",
            "python3 -c \"open('notes.md','w').write('x')\"",
            False,
            "python file write from Bash",
        ),
        # A committed script in one of the repo's `.worktrees/<name>/` checkouts, reached through
        # a variable: the command refused in production on 2026-10-13. Run here because this is
        # the compiled policy, and the `indexof`/`substring` arithmetic that resolves `$W` is only
        # proven in the OPA interpreter by the Rego suite.
        PolicyCase(
            "allow-worktree-script-through-a-variable",
            "cd /home/banon/projects/er-mods-rs && S=/tmp/x; W=.worktrees; "
            "( python3 $W/pr-hook-shim/scripts/test-cupcake-hook-shim.py > $S/hs.log 2>&1; echo done )",
            True,
        ),
        PolicyCase(
            "deny-worktree-traversal-through-a-variable",
            "W=.worktrees; python3 $W/../../tmp/scripts/x.py",
            False,
            "python file write from Bash",
        ),
        # Both allowed until 2026-10-13: a quoted path hid the operand, and a `-c` anywhere in
        # the command switched the script-file rule off.
        PolicyCase(
            "deny-quoted-scratch-script",
            'python3 "/tmp/claude-1000/scratchpad/patch.py"',
            False,
            "python file write from Bash",
        ),
        PolicyCase(
            "deny-scratch-script-inside-bash-c",
            'bash -c "python3 /tmp/claude-1000/scratchpad/patch.py"',
            False,
            "python file write from Bash",
        ),
    ])

    # --- the Bash spelling of a Rust edit (bd er-effects-rs-wuij) --------------------------
    #
    # The Frida gate routed on the write tools and denied on `tool_input.file_path`, which a
    # Bash call does not have, so the rule was undefined for every shell spelling of the edit.
    # The first case is the loop that rewrote four crate sources with no measurement behind it
    # on 2026-09-21, minutes before the Edit tool was refused for the same change.
    #
    # `CUPCAKE_RUNTIME_EVIDENCE_OVERRIDE` does not reach this guard -- it reads the
    # `frida_evidence` signal. That signal used to measure whatever Frida history this machine
    # held, and this block was safe only by accident: the deny cases happen to want the verdict a
    # freshly committed tree already has, and the allow cases below deliberately avoid depending
    # on the verdict at all, by naming paths the rule does not cover. Neither is load-bearing any
    # more. `run_case` now pins the verdict absent for every case through `ER_FRIDA_EVIDENCE_LOG`,
    # so these assert against a fixed reading rather than a lucky one -- see the fixtures beside
    # `WORKTREE_FIXTURE`, and the runner-only failure that forced them.
    cases.extend([
        PolicyCase(
            "deny-bash-sed-in-place-on-a-crate-source",
            "for c in er-refill-all er-npc-possess; do sed -i 's/^const A/pub(crate) const A/' crates/$c/src/config.rs; done",
            False,
            "no Frida measurement behind it",
        ),
        PolicyCase(
            "deny-bash-heredoc-redirect-into-a-crate-source",
            "cat > crates/er-title-flow/src/lib.rs <<'EOF'\npub fn x() {}\nEOF",
            False,
            "no Frida measurement behind it",
        ),
        PolicyCase(
            "deny-bash-git-checkout-of-a-crate-source",
            "git checkout -- crates/er-title-flow/src/lib.rs",
            False,
            "no Frida measurement behind it",
        ),
        PolicyCase(
            "allow-bash-read-of-a-crate-source",
            "sed -n '1,40p' crates/er-title-flow/src/lib.rs",
            True,
        ),
        PolicyCase(
            "allow-bash-diff-of-a-crate-source",
            "git diff crates/er-title-flow/src/lib.rs",
            True,
        ),
        PolicyCase(
            "allow-bash-write-of-a-non-rust-file-under-crates",
            "cat > crates/er-title-flow/Cargo.toml <<'EOF'\n[package]\nEOF",
            True,
        ),
    ])

    # --- resolving a conflict of a stopped merge --------------------------------------------
    #
    # Measured 2026-10-07: a `git rebase --onto` stopped on `crates/er-npc-summons/src/addr.rs`
    # and the Edit deleting the conflict markers was refused with `UNPROVEN spent-by-commit`.
    # Both cases run the real signal against a real stopped merge, with the Frida log absent, so
    # the only thing that can open the gate is the conflict itself.
    cases.extend([
        PolicyCase(
            "allow-edit-resolving-a-conflicted-crate-source",
            "",
            True,
            extra_tool_input={
                "file_path": str(CONFLICTED_RUST),
                "old_string": "<<<<<<< HEAD\npub const A: u32 = 2;\n=======\n",
                "new_string": "pub const A: u32 = 2;\n",
            },
            include_timeout=False,
            tool_name="Edit",
        ),
        PolicyCase(
            "deny-edit-of-an-unconflicted-crate-source-during-the-same-merge",
            "",
            False,
            "no Frida measurement behind it",
            extra_tool_input={
                "file_path": str(CLEAN_RUST_IN_CONFLICTED_REPO),
                "old_string": "mod addr;\n",
                "new_string": "mod addr;\nmod extra;\n",
            },
            include_timeout=False,
            tool_name="Edit",
        ),
        # gh_pr_title_conventional through the live WASM engine (2026-10-13). The kitty hand-off
        # below was refused naming the helper's own `--title`, not the PR title. The PR title is
        # the `--title` of the `gh pr create` inside the quoted argument, and that one is typed.
        # The deny side uses `gh pr edit`: a body-less `gh pr create` is refused by the machine-
        # global attribution guard before the project policies run, so it would never reach this
        # rule on a box that has that guard installed.
        PolicyCase(
            "allow-kitty-handoff-of-a-typed-pr-title",
            "cd /home/banon/projects/er-mods-rs/.worktrees/pr-kitty-tabs && python3 "
            "scripts/kitty-user-command.py --title 'push python-write guard fix' --why 'The agent "
            "may not push. ...' -- 'git -C /home/banon/projects/er-mods-rs push -u origin fix/x && "
            "gh pr create -R Banon-Labs/er-mods-rs --draft --base main --head fix/x --title "
            "\"fix(cupcake): admit committed scripts in .worktrees checkouts; close six "
            "scratch-script escapes\" --body-file /tmp/pr.md'",
            True,
        ),
        PolicyCase(
            "deny-kitty-handoff-of-an-untyped-pr-title",
            "cd /home/banon/projects/er-mods-rs/.worktrees/pr-kitty-tabs && python3 "
            "scripts/kitty-user-command.py --title 'push python-write guard fix' --why 'The agent "
            "may not push. ...' -- 'git -C /home/banon/projects/er-mods-rs push -u origin fix/x && "
            "gh pr create -R Banon-Labs/er-mods-rs --draft --base main --head fix/x --title "
            "\"push it\" --body-file /tmp/pr.md'",
            False,
            "title : push it",
        ),
        PolicyCase(
            "deny-gh-pr-edit-with-an-untyped-title",
            'gh pr edit 421 --title "bad"',
            False,
            "title : bad",
        ),
    ])

    # The GitHub attribution guard is machine-global (XDG config, Banon-Labs/cupcake-config), not
    # repo-local, so CI checkouts do not have it and a footerless gh body is (correctly) allowed
    # there. Exercise its heredoc-substitution fallback only where that policy is installed.
    attribution_policy = (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "cupcake"
        / "policies"
        / "claude"
        / "github_attribution_guard.rego"
    )
    if attribution_policy.is_file():
        cases.extend(
            [
                # A --body "$(cat <<'EOF'...)" command substitution cannot be expanded by the
                # gh_context signal, which falls back to matching the raw command text
                # (2026-07-05 false positive: footer present in the heredoc was denied).
                # Footer present -> allow.
                PolicyCase(
                    "allow-gh-pr-edit-heredoc-substitution-body-with-footer",
                    'gh pr edit 19 --repo Banon-Labs/er-mods-rs --body "$(cat <<\'EOF\'\n'
                    "## What changed\n\nA thing.\n\n"
                    "## Why\n\nBecause.\n\n"
                    "## Evidence\n\nNot proven: nothing was run.\n\n"
                    "\U0001f916 Written by Claude Fable 5, authorized by @chozandrias76\n"
                    'EOF\n)"',
                    True,
                ),
                # ... and the same form without the footer must still deny (the raw
                # command fallback must not weaken the guard).
                PolicyCase(
                    "deny-gh-pr-edit-heredoc-substitution-body-without-footer",
                    # Template-complete on purpose, so the only thing missing is the footer and the
                    # denial can be attributed to the guard under test rather than to the PR body
                    # contract, which would deny a headingless body for its own reasons.
                    'gh pr edit 19 --repo Banon-Labs/er-mods-rs --body "$(cat <<\'EOF\'\n'
                    "## What changed\n\nA thing.\n\n"
                    "## Why\n\nBecause.\n\n"
                    "## Evidence\n\nNot proven: nothing was run.\n"
                    'EOF\n)"',
                    False,
                    "attribution footer",
                ),
            ]
        )
    else:
        print(f"skip: gh-attribution guard cases (no global policy at {attribution_policy})")
    width = pool_width(len(cases), _permitted_cores(), _load_average(), os.cpu_count(),
                       os.environ.get("CUPCAKE_POLICY_TEST_JOBS"))
    print(f"cupcake policy cases: {len(cases)} on {width} worker(s)", flush=True)
    failures = run_cases(cases, run_case, width)
    events = _OPA_BUILD_CACHE_DIR / "events"
    if events.is_file():
        tally = events.read_text(encoding="utf-8").split()
        print(f"opa build cache: {tally.count('miss')} compile(s), {tally.count('hit')} reuse(s)")
    if failures:
        raise AssertionError(f"{len(failures)} policy case(s) failed:\n\n" + "\n\n".join(sorted(failures)))
    print("cupcake policy regression tests passed")
    return 0


# 12, measured, not guessed: on 2026-08-31, 16 cores under a hostile loadavg of ~100, wall clock
# over the whole case list was 8 workers 26.5s | 12 workers 20.4s | 16 workers 21.8s | 24 workers
# 21.6s. Scaling flattens past 12 and then reverses, so there is nothing to win by going wider.
POOL_WIDTH_CEILING = 12
# Never below 4, however loaded the machine reads. A loadavg reading is a minute-old average, and
# the timeout this sizing protects is per eval, which a cached eval (~0.4s alone) is far from at
# width 4. Runs on 2026-10-20 under 28 busy loops pinned to 14 of 16 cores, with other sessions
# also loading the box: width 2 passed in 168s (loadavg 32), width 4 in 252s (loadavg 61), no
# retries in either. The wall clock follows the machine's other load more than this width.
POOL_WIDTH_FLOOR = 4


def _permitted_cores() -> int | None:
    """The affinity mask, which is what scripts/lib/cpu-courtesy.sh narrows for a gate run."""
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return None


def _load_average() -> float | None:
    try:
        return os.getloadavg()[0]
    except (AttributeError, OSError):
        return None


def pool_width(n_cases: int, permitted: int | None, load1: float | None, machine_cores: int | None,
               override: str | None = None) -> int:
    """How many evals to run at once: the cores that are actually free, not the cores that exist.

    The width used to be `min(12, affinity)`. On 2026-10-20 that put 8 concurrent evals onto a
    machine where the pre-push hook was running ten stages in parallel and another session's full
    check.sh was running too, and a case timed out. Each eval is a multi-threaded process (opa
    build was ~2 CPU-seconds of it before the compile cache), so a pool sized to the mask
    oversubscribes a machine whose other users already hold the mask. The free share is the
    machine's cores minus its one-minute load, capped by the mask, the ceiling above and the case
    count. `CUPCAKE_POLICY_TEST_JOBS` overrides outright.
    """
    if override and override.strip().isdigit() and int(override) > 0:
        return min(int(override), max(1, n_cases))
    width = POOL_WIDTH_CEILING
    if permitted:
        width = min(width, permitted)
    if load1 is not None and machine_cores:
        width = min(width, max(POOL_WIDTH_FLOOR, int(machine_cores - load1)))
    return max(1, min(width, n_cases))


def run_cases(cases: Sequence[PolicyCase], runner: Callable[[PolicyCase], None], width: int,
              log: Callable[[str], None] = lambda line: print(line, file=sys.stderr, flush=True)) -> list[str]:
    """Run every case on the pool, re-run each timed-out one once on its own, return the failures.

    A timeout inside the pool says the machine was busy as often as it says the case hung, so it
    gets a second attempt after the pool has drained, alone, with nothing of this suite competing
    with it. That attempt is logged. A case that times out again fails, so a real hang in a policy
    or a signal is still a red gate and not a silent retry loop.

    Every failing case is reported, not only the first to finish: a rule change that moves several
    verdicts at once should be read as one list.
    """
    failures: list[str] = []
    timed_out: list[PolicyCase] = []
    with ThreadPoolExecutor(max_workers=max(1, width)) as pool:
        futures = {pool.submit(runner, case): case for case in cases}
        for future in as_completed(futures):
            try:
                future.result()
            except CaseTimedOut:
                timed_out.append(futures[future])
            except AssertionError as exc:
                failures.append(str(exc))
    for case in sorted(timed_out, key=lambda c: c.name):
        log(f"retry: {case.name} timed out after {CASE_TIMEOUT_SECONDS}s in the pool; "
            "running it once more on its own")
        try:
            runner(case)
        except CaseTimedOut:
            failures.append(f"{case.name}: timed out after {CASE_TIMEOUT_SECONDS}s twice, the second "
                            "time running alone -- a hang, not load")
        except AssertionError as exc:
            failures.append(str(exc))
        else:
            log(f"retry: {case.name} passed on its own")
    return failures


def selftest() -> int:
    """The pool sizing, the timeout retry and the compile cache, without one real policy case."""
    problems: list[str] = []

    def expect(label: str, got: object, want: object) -> None:
        if got != want:
            problems.append(f"{label}: got {got!r}, want {want!r}")

    # Pool width.
    expect("idle 16-core box", pool_width(176, 16, 0.5, 16), 12)
    expect("courtesy mask of 8", pool_width(176, 8, 0.5, 16), 8)
    expect("half the machine busy", pool_width(176, 16, 10.0, 16), 6)
    expect("2026-10-20 pre-push load", pool_width(176, 8, 36.6, 16), POOL_WIDTH_FLOOR)
    expect("no loadavg", pool_width(176, 8, None, 16), 8)
    expect("no affinity", pool_width(176, None, 2.0, 16), 12)
    expect("fewer cases than cores", pool_width(3, 16, 0.0, 16), 3)
    expect("override", pool_width(176, 8, 36.6, 16, "5"), 5)
    expect("junk override ignored", pool_width(176, 8, 0.0, 16, "lots"), 8)

    # Retry: a case that times out once passes on its retry and is logged; one that times out twice
    # fails; an ordinary failure is not retried.
    attempts: dict[str, int] = {}

    def fake_runner(case: PolicyCase) -> None:
        attempts[case.name] = attempts.get(case.name, 0) + 1
        if case.name == "flaky" and attempts[case.name] == 1:
            raise CaseTimedOut(case.name)
        if case.name == "hang":
            raise CaseTimedOut(case.name)
        if case.name == "wrong":
            raise AssertionError("wrong: expected allow=True")

    logged: list[str] = []
    fake_cases = [PolicyCase(n, "true", True) for n in ("ok", "flaky", "hang", "wrong")]
    failures = run_cases(fake_cases, fake_runner, 4, logged.append)
    expect("attempts", attempts, {"ok": 1, "flaky": 2, "hang": 2, "wrong": 1})
    expect("failures", sorted(f.split(":")[0] for f in failures), ["hang", "wrong"])
    expect("hang named as a hang", any("twice" in f for f in failures if f.startswith("hang")), True)
    expect("retry logged", sum("retry: flaky" in line for line in logged), 2)
    expect("retry of a real hang logged", any("retry: hang timed out" in line for line in logged), True)

    # The compile cache, against a fake `opa` that counts its compiles. Same tree in two different
    # directories hits; one changed byte misses; a non-build call passes straight through.
    with tempfile.TemporaryDirectory(prefix="opa-build-cache-selftest-") as tmp:
        root = Path(tmp)
        counter = root / "compiles"
        fake_opa = root / "opa"
        fake_opa.write_text(
            "#!/usr/bin/env bash\n"
            f"echo x >> {counter}\n"
            'if [ "$1" = build ]; then out=""; prev=""; for a in "$@"; do\n'
            '  [ "$prev" = -o ] && out="$a"; prev="$a"; done\n'
            '  cat "${@: -3:1}"/*.rego > "$out"; else echo passthrough; fi\n',
            encoding="utf-8")
        fake_opa.chmod(0o755)
        cache = root / "cache"
        env = {**os.environ, "ER_OPA_REAL": str(fake_opa), "ER_OPA_BUILD_CACHE": str(cache)}

        def build(tag: str, content: str) -> str:
            d = root / tag
            d.mkdir()
            (d / "p.rego").write_text(content, encoding="utf-8")
            subprocess.run(["bash", str(OPA_BUILD_CACHE_SHIM), "build", "-t", "wasm", "-e", "x", str(d),
                            "-o", str(d / "bundle.tar.gz")], env=env, check=True, capture_output=True,
                           timeout=10)
            return (d / "bundle.tar.gz").read_text(encoding="utf-8")

        def compiles() -> int:
            return len(counter.read_text(encoding="utf-8").split()) if counter.exists() else 0

        expect("first build output", build("a", "package one\n"), "package one\n")
        expect("first build compiles", compiles(), 1)
        expect("same tree elsewhere output", build("b", "package one\n"), "package one\n")
        expect("same tree elsewhere reuses", compiles(), 1)
        expect("changed tree output", build("c", "package two\n"), "package two\n")
        expect("changed tree compiles", compiles(), 2)
        passthrough = subprocess.run(["bash", str(OPA_BUILD_CACHE_SHIM), "version"], env=env,
                                     capture_output=True, text=True, timeout=10)
        expect("non-build passes through", passthrough.stdout.strip(), "passthrough")
        expect("events", (cache / "events").read_text(encoding="utf-8").split(), ["miss", "hit", "miss"])
    expect("argv without opa", "--opa-path" in cupcake_eval_argv(None), False)
    expect("argv with opa", cupcake_eval_argv("/usr/bin/opa")[2:4], ["--opa-path", str(OPA_BUILD_CACHE_SHIM)])

    for p in problems:
        print(f"FAIL {p}", file=sys.stderr)
    if problems:
        return 1
    print("test-cupcake-policies selftest: pool sizing, timeout retry and opa build cache ok")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        raise SystemExit(selftest())
    raise SystemExit(main())
