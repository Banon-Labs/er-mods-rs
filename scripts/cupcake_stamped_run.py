#!/usr/bin/env python3
"""Has the Elden Ring run that is up already stamped the commit its branch will push?

Read by `.cupcake/signals/stamped_run.sh`, consumed by
`.cupcake/policies/claude/teardown_must_relaunch.rego`, which admits a standalone
`er-teardown.py --reason=stamped-<token>` only when this prints a `PROVEN` line.

Why the guard needs it. AGENTS.md says to close Elden Ring before pushing, because
`scripts/check.sh`, which the pre-push hook runs, exits 2 while `er-teardown.py --status` reports a
game. And AGENTS.md also says a push of crate code waits for a run of that exact tip ("The phrase
waits for a run of that exact tip"). So the agent launches a run to stamp the commit, the stamp
lands, the user replies `authorize <sha>`, and the run has to end before the push. The teardown
guard admitted a standalone teardown only after a Frida measurement or for a run declared invalid,
and a stamping run is neither. Measured 2026-10-22: run br-20261022-023259-d732 loaded
er_quickload.dll built at a9be7625, `check-runtime-evidence.sh a9be7625...` exited 0 in
`.worktrees/feat-asset-geometry-tooling`, the user authorized the push, and
`er-teardown.py --reason=push-needs-game-closed` was refused, so the teardown had to be handed to
the user in a kitty tab.

Output, one line:

    `PROVEN <run id> <branch> <head12> <checkout>`
    `UNPROVEN <reason> [<detail>]`

What `PROVEN` requires, each condition refusing on its own:

  1. The newest `br-*` run under the run root is up: its `run.json` is still there (the detached
     reaper, or the next launch's garbage collection, removes it when the run ends) and the launcher
     pid recorded in it is alive. A run the user started through `~/Elden/launch.sh` writes no
     `br-*` directory, so it can never be the run this admits a teardown of.
  2. At least one of that run's own logs opens with a `build git=<sha>` line: the DLL loaded and
     stamped. A run still booting has not.
  3. The run was launched on a branch (`meta.branch` in `run.json`, which `er-run-branch.py` takes
     from its own checkout), and a checkout in `git worktree list` has that branch checked out.
  4. One of the run's stamped shas is that checkout's `HEAD` or an ancestor of it, so the run is of
     the code that branch now holds.
  5. That checkout's own `scripts/check-runtime-evidence.sh` -- the pre-push hook's script, run the
     way the hook runs it, from the checkout's top with the full `HEAD` sha and git's
     repository-pinning variables unset -- passes, classified the way
     `.cupcake/signals/runtime_evidence_for_head.sh` classifies it: exit 0 with a report that is
     neither empty (no crate changed, so nothing for a run to stamp) nor `unmeasurable`. The
     `ER_ALLOW_UNPROVEN_PUSH` override is removed from its environment, since a waiver is not a
     stamp.
  6. The evidence is this run's: one of its own stamps names `HEAD`, or the report says the
     evidence was carried forward from a log under this run's directory. A pass that rests only on
     an older run, or on a provenance record matched to some other log, proves that the push may
     go and says nothing about why this run is up, so it is refused.

Fails closed. Every refusal prints `UNPROVEN`, and a crash or a timeout prints nothing, which the
policy reads the same way. There is no override variable that prints `PROVEN`, for the reason
`.cupcake/signals/frida_evidence.sh` gives: a variable that hands out the exception is the hole.
The tests build a real repository and run root and point `ER_ME3_RUN_ROOT` (the run-root override
`scripts/er-runtime-evidence.py` already reads) and `ER_GAME_DIR` at them, so the verdict always
comes from the checks above.

Usage:
    python3 scripts/cupcake_stamped_run.py < event.json
    python3 scripts/cupcake_stamped_run.py --selftest
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import er_run_lib  # noqa: E402 -- `process_alive`, the one /proc liveness reader in this repo

BUILD_LINE = re.compile(r"^build git=([0-9a-f]{7,40})")

GIT_TIMEOUT_SECONDS = 5.0
# Under the 30 s the rulebook gives this signal, so a slow carry-forward scan ends here as an
# `UNPROVEN` rather than as a dropped signal.
EVIDENCE_TIMEOUT_SECONDS = 22.0

# The variables git reads before `-C`. A signal can run inside a git hook's environment, where
# `GIT_DIR` would pin every read below to that repository whatever directory is named.
GIT_PINNING = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
)


def run_root() -> Path:
    """The branch-launch run root, under the override `er-runtime-evidence.py` reads."""
    return Path(os.environ.get("ER_ME3_RUN_ROOT") or Path.home() / ".cache" / "er-me3-runs")


def clean_env(base: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k not in GIT_PINNING}
    # The deliberate override turns the evidence script's refusal into an allow; it is not a stamp.
    env.pop("ER_ALLOW_UNPROVEN_PUSH", None)
    return env


def git(directory: Path | str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def newest_run(root: Path) -> Path | None:
    try:
        runs = [d for d in root.iterdir() if d.is_dir() and d.name.startswith("br-")]
    except OSError:
        return None
    # `br-<UTC yyyymmdd-hhmmss>-<hex>`: the name sorts by launch time.
    return max(runs, key=lambda d: d.name) if runs else None


def stamps(run: Path) -> list[str]:
    """The `build git=` shas the run's own logs open with, in file-name order, deduplicated."""
    found: list[str] = []
    for log in sorted(run.glob("er-*.log")):
        try:
            with log.open(encoding="utf-8", errors="replace") as handle:
                first = handle.readline()
        except OSError:
            continue
        match = BUILD_LINE.match(first)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def worktrees(directory: Path | str, env: dict[str, str]) -> list[tuple[str, str]]:
    """`(path, branch ref)` for every checkout `git worktree list` names from `directory`."""
    listing = git(directory, "worktree", "list", "--porcelain", env=env)
    if listing is None or listing.returncode != 0:
        return []
    found: list[tuple[str, str]] = []
    path = ""
    for line in listing.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch ") and path:
            found.append((path, line[len("branch "):]))
    return found


def checkout_for(branch: str, directories: list[str], env: dict[str, str]) -> str | None:
    wanted = f"refs/heads/{branch}"
    for directory in directories:
        if not directory or not os.path.isdir(directory):
            continue
        for path, ref in worktrees(directory, env):
            if ref == wanted and os.path.isdir(path):
                return path
    return None


def evidence(checkout: str, head: str, env: dict[str, str]) -> tuple[int | None, str]:
    """Exit status and stderr of the checkout's own pre-push evidence script for `head`."""
    script = Path(checkout) / "scripts" / "check-runtime-evidence.sh"
    if not script.is_file():
        script = REPO_ROOT / "scripts" / "check-runtime-evidence.sh"
    try:
        proc = subprocess.Popen(
            ["bash", str(script), head],
            cwd=checkout,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            # Its own process group, so a timeout ends the python and git children the script
            # spawned rather than only the bash that started them.
            start_new_session=True,
        )
    except OSError:
        return None, ""
    try:
        _, err = proc.communicate(timeout=EVIDENCE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        return None, "timed out"
    return proc.returncode, err or ""


def decide(event: dict, root: Path, env: dict[str, str]) -> str:
    run = newest_run(root)
    if run is None:
        return f"UNPROVEN no-run {root}"
    state_file = run / "run.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"UNPROVEN not-up {run.name} has no run.json, so it has ended"
    if not isinstance(state, dict):
        return f"UNPROVEN not-up {run.name} has an unreadable run.json"
    try:
        pid = int(state.get("pid", 0))
    except (TypeError, ValueError):
        pid = 0
    if not er_run_lib.process_alive(pid):
        return f"UNPROVEN not-up {run.name} launcher pid {pid} is not alive"

    shas = stamps(run)
    if not shas:
        return f"UNPROVEN no-stamp {run.name} has no log opening with a build line yet"

    meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    branch = meta.get("branch") if isinstance(meta.get("branch"), str) else ""
    if not branch or branch == "HEAD":
        return f"UNPROVEN no-branch {run.name} was not launched on a branch"

    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
    checkout = checkout_for(branch, [cwd, str(REPO_ROOT)], env)
    if checkout is None:
        return f"UNPROVEN no-checkout no worktree has {branch} checked out"

    resolved = git(checkout, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", env=env)
    if resolved is None or resolved.returncode != 0 or not resolved.stdout.strip():
        return f"UNPROVEN no-head {checkout}"
    head = resolved.stdout.strip()

    on_branch = False
    for sha in shas:
        ancestry = git(checkout, "merge-base", "--is-ancestor", sha, head, env=env)
        if ancestry is not None and ancestry.returncode == 0:
            on_branch = True
            break
    if not on_branch:
        return (
            f"UNPROVEN stamp-off-branch {run.name} stamped {','.join(s[:12] for s in shas)}, "
            f"none of which is {branch} at {head[:12]} or an ancestor of it"
        )

    status, report = evidence(checkout, head, env)
    if status != 0:
        note = next(
            (line.strip()[len("evidence "):].strip() for line in report.splitlines()
             if line.strip().startswith("evidence ")),
            report.strip().splitlines()[-1] if report.strip() else f"exit {status}",
        )
        return f"UNPROVEN no-evidence {head[:12]} {note}"
    if not report.strip():
        return f"UNPROVEN no-stamp-needed {head[:12]} changes no crate, so no run stamps it"
    if "unmeasurable" in report:
        return f"UNPROVEN unmeasurable {head[:12]} the evidence script could not measure"

    names_head = any(head.startswith(sha) for sha in shas)
    # `check-runtime-evidence.sh` names a carried-forward run by its candidate's `where`, which
    # `er-runtime-evidence.py` writes as `<run directory>/<log>`.
    carried_from_run = any(
        "carried forward" in line and f"-- {run.name}/" in line for line in report.splitlines()
    )
    if not (names_head or carried_from_run):
        return (
            f"UNPROVEN not-this-run {head[:12]} passes on evidence that is not {run.name}'s: "
            f"{report.strip().splitlines()[-1]}"
        )
    return f"PROVEN {run.name} {branch} {head[:12]} {checkout}"


# ------------------------------------------------------------------------------------------
# Fixtures, shared with `scripts/test-cupcake-policies.py`
# ------------------------------------------------------------------------------------------

FIXTURE_BRANCH = "policy-regression/stamped-run-fixture"
# No artifact of this name exists in any `target/`, so `er-runtime-evidence.py` can only accept the
# fixture log by the sha on its build line.
FIXTURE_MODULE = "er_stamped_run_fixture.dll"
# A crate a shell compiles, so the evidence scan asks a run for it rather than waving it through
# as host-only code.
FIXTURE_CRATE_FILE = "crates/er-quickload/src/stamped_run_fixture.rs"
# Above the largest `pid_max` a 64-bit kernel accepts (2**22), so no process can ever hold it.
DEAD_PID = 2**22 + 1


def build_fixture_repo(root: Path, kind: str = "crate") -> dict[str, str]:
    """A repository with `origin/main` at a base commit and `FIXTURE_BRANCH` checked out above it.

    `crate`: one commit adding `FIXTURE_CRATE_FILE`. `docs`: one commit adding a file under
    `docs/`, so no crate changes. `carried`: the `crate` commit, then one adding a file under
    `scripts/`, which `check-runtime-evidence.sh` carries a run of the first forward across.
    Returns the checkout and the shas: `base`, `crate` (for `carried`) and `head`.
    """
    repo = root / f"repo-{kind}"
    repo.mkdir(parents=True)
    env = clean_env(dict(os.environ))

    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=stamped-run", "-c", "user.email=stamped-run@invalid",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=repo, capture_output=True, text=True, check=True, timeout=10, env=env,
        ).stdout.strip()

    def commit(path: str, message: str) -> str:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{message}\n", encoding="utf-8")
        run("add", "-A")
        run("commit", "-q", "-m", message)
        return run("rev-parse", "HEAD")

    run("init", "-q", "-b", "base")
    shas = {"repo": str(repo), "base": commit("README", "base")}
    run("update-ref", "refs/remotes/origin/main", shas["base"])
    run("checkout", "-q", "-b", FIXTURE_BRANCH)
    if kind == "docs":
        shas["head"] = commit("docs/stamped-run-fixture.md", "docs change")
    else:
        shas["crate"] = shas["head"] = commit(FIXTURE_CRATE_FILE, "crate change")
        if kind == "carried":
            shas["head"] = commit("scripts/stamped-run-fixture.txt", "scripts change")
    return shas


def write_run(
    runs: Path,
    run_id: str,
    *,
    pid: int,
    branch: str = FIXTURE_BRANCH,
    stamp: str | None = None,
    state: bool = True,
) -> Path:
    """One `br-*` run directory: `run.json` (unless `state` is false) and one DLL log."""
    run = runs / run_id
    run.mkdir(parents=True)
    if state:
        (run / "run.json").write_text(
            json.dumps({"run_id": run_id, "pid": pid, "profile": "", "remove_paths": [],
                        "meta": {"branch": branch, "evidence_class": "fixture"}}),
            encoding="utf-8",
        )
    first = f"build git={stamp[:12]} module={FIXTURE_MODULE} base=0x0\n" if stamp else "booting\n"
    (run / "er-quickload-autoload-debug.log").write_text(first + "later line\n", encoding="utf-8")
    return run


def selftest() -> int:
    failures = 0

    def check(condition: bool, label: str, got: str) -> None:
        nonlocal failures
        if not condition:
            failures += 1
        print(("  ok   " if condition else "  FAIL ") + label + ("" if condition else f"\n         got: {got}"))

    alive = os.getpid()
    with tempfile.TemporaryDirectory(prefix="stamped-run-selftest-") as raw:
        tmp = Path(raw)
        fixture = build_fixture_repo(tmp)
        docs = build_fixture_repo(tmp, "docs")
        carried = build_fixture_repo(tmp, "carried")
        game = tmp / "game"
        game.mkdir()
        base_env = clean_env(dict(os.environ))
        base_env["ER_GAME_DIR"] = str(game)

        def verdict(name: str, repo: str, build) -> str:
            runs = tmp / f"runs-{name}"
            runs.mkdir()
            build(runs)
            env = {**base_env, "ER_ME3_RUN_ROOT": str(runs)}
            return decide({"cwd": repo}, runs, env)

        repo, head, base = fixture["repo"], fixture["head"], fixture["base"]

        got = verdict("proven", repo, lambda r: write_run(r, "br-20990101-000000-0001", pid=alive, stamp=head))
        check(got.startswith("PROVEN br-20990101-000000-0001 ") and head[:12] in got,
              "a live run whose log names the branch tip proves it", got)

        got = verdict("absent", repo, lambda r: None)
        check(got.startswith("UNPROVEN no-run"), "an empty run root proves nothing", got)

        got = verdict("ended", repo, lambda r: write_run(r, "br-20990101-000000-0002", pid=alive, stamp=head, state=False))
        check(got.startswith("UNPROVEN not-up"), "a run whose run.json is gone has ended", got)

        got = verdict("dead", repo, lambda r: write_run(r, "br-20990101-000000-0003", pid=DEAD_PID, stamp=head))
        check(got.startswith("UNPROVEN not-up"), "a run whose launcher is dead is not the one up", got)

        def older_proven_newer_dead(r: Path) -> None:
            write_run(r, "br-20990101-000000-0004", pid=alive, stamp=head)
            write_run(r, "br-20990101-000001-0005", pid=DEAD_PID, stamp=head)

        got = verdict("newest", repo, older_proven_newer_dead)
        check(got.startswith("UNPROVEN not-up br-20990101-000001-0005"),
              "only the newest run is asked, even when an older one would prove", got)

        got = verdict("booting", repo, lambda r: write_run(r, "br-20990101-000000-0006", pid=alive))
        check(got.startswith("UNPROVEN no-stamp"), "a run with no build line has not stamped", got)

        got = verdict("detached", repo, lambda r: write_run(r, "br-20990101-000000-0007", pid=alive, branch="HEAD", stamp=head))
        check(got.startswith("UNPROVEN no-branch"), "a run launched from a detached HEAD is refused", got)

        got = verdict("elsewhere", repo, lambda r: write_run(r, "br-20990101-000000-0008", pid=alive, branch="no/such-branch", stamp=head))
        check(got.startswith("UNPROVEN no-checkout"), "a branch no checkout holds is refused", got)

        got = verdict("unrelated", repo, lambda r: write_run(r, "br-20990101-000000-0009", pid=alive, stamp="0123456789ab"))
        check(got.startswith("UNPROVEN stamp-off-branch"), "a stamp that is not on the branch is refused", got)

        # The stamp is the base, an ancestor of the tip, and the tip adds a crate change on top of
        # it: on the branch, but nothing ran the tip, so the evidence script refuses.
        got = verdict("ancestor", repo, lambda r: write_run(r, "br-20990101-000000-0010", pid=alive, stamp=base))
        check(got.startswith("UNPROVEN no-evidence"), "a stamp below a crate change does not prove the tip", got)

        got = verdict("docs", docs["repo"], lambda r: write_run(r, "br-20990101-000000-0011", pid=alive, stamp=docs["head"]))
        check(got.startswith("UNPROVEN no-stamp-needed"),
              "a branch that changes no crate needs no stamp, so its run is not a stamping run", got)

        # The run stamped the crate commit, and the tip adds only a `scripts/` file on top of it.
        got = verdict("carried", carried["repo"], lambda r: write_run(r, "br-20990101-000000-0012", pid=alive, stamp=carried["crate"]))
        check(got.startswith("PROVEN br-20990101-000000-0012 ") and carried["head"][:12] in got,
              "a run carried forward to the tip proves it", got)

        # The tip is proven, but by an older run that has ended. The run that is up stamped only
        # the base, so the pass says nothing about why it is up.
        def older_ended_proves(r: Path) -> None:
            write_run(r, "br-20990101-000000-0013", pid=alive, stamp=head, state=False)
            write_run(r, "br-20990101-000001-0014", pid=alive, stamp=base)

        got = verdict("older", repo, older_ended_proves)
        check(got.startswith("UNPROVEN not-this-run"),
              "a pass resting on an older run's log does not prove the run that is up", got)

        check(clean_env({"GIT_DIR": "/x", "ER_ALLOW_UNPROVEN_PUSH": "1", "HOME": "/h"}) == {"HOME": "/h"},
              "git's pinning variables and the unproven-push override never reach the checks", "")

    print("selftest: " + ("ok" if failures == 0 else f"{failures} failure(s)"))
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    event: dict = {}
    if not sys.stdin.isatty():
        try:
            parsed = json.loads(sys.stdin.read() or "{}")
            if isinstance(parsed, dict):
                event = parsed
        except ValueError:
            pass
    print(decide(event, run_root(), clean_env(dict(os.environ))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
