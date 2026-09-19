#!/usr/bin/env python3
"""Which stage of the suite does each scripts/check.sh step belong to?

Why this exists. `scripts/check.sh` is one process that runs every gate in the repo, and its
verdict arrives all at once, at the end, after everything slow has finished. A formatting mistake
and a broken cross-compile are reported the same distance from the push that caused them. The user
asked for the opposite: "I would like to know when specific things fail faster, and it really just
encapsulates too much."

So the suite is partitioned into named stages. A stage is a subset of check.sh's own step lines,
runnable on its own (`bash scripts/check.sh --stage lint`), cacheable on its own, and visible as
its own job on a GitHub run page. Nothing about the gates themselves changed: the step list is
still the one place a gate is written down, and `bash scripts/check.sh` with no arguments still
runs all of it.

The anti-drift property is the point, and it is the reason this file exists rather than a stage
list written into `.github/workflows/check.yml`. This repo has been bitten three times by a
hand-copied list of gate names falling behind the suite it was copied from:

  - `.github/workflows/check.yml` named 9 gates while check.sh ran 224, so ~215 gates were
    invisible to CI and nothing said so.
  - `scripts/ci-local-check.sh` named 13 while check.sh referenced 153, which is how a
    `check-fnv1a-owner.py` violation reached origin through the pre-push hook (PR #398).
  - check.sh's own shellcheck coverage is a hand list and had silently dropped
    `scripts/opa-query.sh`.

A stage table is exactly that shape of list, so it gets exactly the enforcement the portability
ledger already has: `--check` fails when a step belongs to no stage, to more than one, or when a
stage is declared and nothing is in it. There is no default bucket and no catch-all, because a
catch-all is how a step stops being classified without anyone deciding that.

Where the assignment lives, and why it is not a second file. Every check.sh step that runs a repo
gate script already has exactly one row in `docs/ci-gate-portability.tsv` -- measured, and held in
bijection with the step list by `ci-gate-portability.py --check` (213 rows, 213 keyed steps, no
duplicates, no orphans, as of this writing). Adding a parallel `docs/check-stages.tsv` would create
a second key set that could disagree with the first, which is the disease rather than the cure. So
the stage is a fifth column on the row that already exists: one row per gate, two measured facts
about it, one bijection to keep honest.

The other 79 steps invoke a tool rather than a repo script -- `cargo`, `shellcheck`, `rustfmt`,
`opa`, `cupcake` -- and the portability ledger deliberately does not carry them, on the grounds
that their only dependency is whether the tool is installed. Their stage is equally uniform, so it
is a rule here rather than 79 rows that all say the same thing:

    shellcheck / rustfmt / cargo fmt   -> lint
    opa / cupcake                      -> policy
    cargo xwin                         -> cargo-build
    every other cargo invocation       -> cargo-test

Usage:
  python3 scripts/check-stages.py --check          # the parity gate
  python3 scripts/check-stages.py --list           # every step with its stage
  python3 scripts/check-stages.py --stages         # stage names, one per line
  python3 scripts/check-stages.py --matrix         # stage names as JSON, for the CI matrix
  python3 scripts/check-stages.py --lines <stage>  # check.sh line numbers in one stage
  python3 scripts/check-stages.py --skip-lines <stage>   # the complement: what --stage must skip
  python3 scripts/check-stages.py --inputs <stage>       # files whose hash keys that stage's cache
  python3 scripts/check-stages.py --stages-for-paths a b # stages those paths can invalidate
  python3 scripts/check-stages.py --stages-for-diff      # ...for the diff against origin/main
  python3 scripts/check-stages.py --audit-inputs   # inputs a stage reads and does not declare
  python3 scripts/check-stages.py --steps-tsv      # `line<TAB>text` for every step, for tooling
  python3 scripts/check-stages.py --selftest       # positive controls for --check
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK_SH = REPO / "scripts" / "check.sh"
LEDGER = REPO / "docs" / "ci-gate-portability.tsv"


def _load_portability():
    """Import `ci-gate-portability.py`, whose name is not an identifier, so `import` cannot.

    Sharing its parser rather than copying it is the whole reason the two files agree about what a
    step is. `step_pattern` reads check.sh's own `_check_step_pattern`, so there is exactly one
    definition of "a step" in the repository and it lives in check.sh.
    """
    path = REPO / "scripts" / "ci-gate-portability.py"
    spec = importlib.util.spec_from_file_location("er_ci_gate_portability", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"check-stages: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because its `@dataclass` decorator resolves annotations through
    # `sys.modules[cls.__module__]`, which raises on python 3.14 when the module is absent there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Stage:
    name: str
    blurb: str
    # Which files this stage reads. A cache key over their content is what lets CI skip a stage
    # whose inputs did not move. Globs are repo-relative and resolved with `Path.glob`.
    inputs: tuple[str, ...]


# The stage list. Ordered cheapest-first as a deliberate scheduling hint: run locally with
# ER_CHECK_JOBS=1 and the first verdict arrives from `lint`, not from a cross-compile.
#
# The boundaries were chosen from the per-step measurement in `scripts/check-step-timings.sh`, on
# two axes -- what a failure means, and what it costs -- rather than from how the gates are named.
# A stage exists when either answer differs enough to be worth a separate job: `lint` is seconds
# and tells you to reformat, `cargo-build` is minutes and tells you the product does not link.
STAGES: tuple[Stage, ...] = (
    Stage(
        "suite",
        "the gate system checking itself: accumulation semantics, the portability ledger, "
        "the timeout cap, the git hooks, the config guard",
        (
            "scripts/check.sh",
            "scripts/hooks/*",
            "scripts/hooks-fallback-shim",
            "scripts/lib/*.sh",
            "scripts/frida/*",
            "scripts/*.py",
            "scripts/*.sh",
            "scripts/*.toml",
            ".githooks/*",
            ".github/workflows/*.yml",
            "crates/**/*",
            "data/*",
            "docs/ci-gate-portability.tsv",
            "docs/recon/**/*",
            # Named by a suite gate in code, and found by `--audit-inputs` rather than by reading:
            # the vendored MinHook source is checked out by the workflow, so it resolves on a runner
            # and in any tree that has cloned it, and a change under it must select this stage.
            "vendor/minhook/**/*",
        ),
    ),
    Stage(
        "lint",
        "formatting and text shape: cargo fmt, rustfmt, shellcheck, comment capitals, "
        "lossy utf-8, markdown, file sizes",
        (
            "**/*.rs",
            "**/*.sh",
            "**/*.bash",
            "**/*.py",
            "**/*.md",
            "rustfmt.toml",
            ".cargo/config.toml",
            "scripts/comment-caps-words.txt",
            "scripts/comment-caps.baseline.json",
        ),
    ),
    Stage(
        "policy",
        "the cupcake rulebook and its OPA suites, plus the prose signals they evaluate",
        (
            ".cupcake/**/*",
            ".auto/*",
            ".claude/settings.json",
            "crates/**/*.rs",
            "crates/**/Cargo.toml",
            "scripts/*.py",
            "scripts/*.sh",
            "scripts/check.sh",
            "scripts/frida/*",
            "scripts/hooks/*",
            "scripts/lib/*.sh",
        ),
    ),
    Stage(
        "docs",
        "ledgers, roadmaps and recon tables: the documents other gates read as truth",
        ("docs/**/*", "AGENTS.md", "README.md", ".beads/*", "crates/**/*"),
    ),
    Stage(
        "moveset",
        "the per-character moveset table, regenerated against the unpacked chr corpus. Its own "
        "stage because of what it costs: 357 of the suite's 695 non-cargo seconds, in one step.",
        ("crates/er-npc-possess/**/*", "scripts/er-moveset-*.py", "scripts/check-moveset-*.py"),
    ),
    Stage(
        "source",
        "repo-wide source invariants that need neither a game image nor a compiler",
        (
            "crates/**/*.rs",
            "build-support/**/*.rs",
            "scripts/*.py",
            "scripts/*.txt",
            "scripts/thread-suspension.baseline.json",
        ),
    ),
    Stage(
        "product",
        "product and release contracts: me3 profiles, the single-DLL rule, shell coverage, "
        "the release workflow",
        (
            "crates/**/*",
            "build-support/**/*",
            "Cargo.toml",
            "data/*",
            ".github/workflows/*.yml",
            "scripts/*.py",
            "scripts/er-build-dlls.sh",
            "scripts/quickload-feature-bite.baseline.json",
            # The two tables the installer is built from. `*.py` above covers the gates that
            # read them but not the data itself, so a push that only edited a conflict row or a
            # catalog label would have skipped this whole stage -- which is the one stage that
            # proves the default selection can be loaded together.
            "scripts/me3-dll-conflicts.toml",
            "scripts/me3-dll-catalog.toml",
        ),
    ),
    Stage(
        "runtime-tools",
        "the launcher, probe and telemetry tooling -- tested here, never run against the game",
        ("scripts/*.py", "scripts/*.sh", ".auto/*", "crates/**/*", "data/*"),
    ),
    Stage(
        "addresses",
        "the game image: RVAs, prologue bytes, struct offsets, the 1.16.2 to 1.17 map. "
        "Most of it cannot run on a runner and says so.",
        (
            "crates/**/*",
            "docs/recon/**/*",
            "build-support/**/*.rs",
            "scripts/*.py",
            "scripts/*.rs",
            "scripts/*.toml",
            "scripts/*.txt",
            "scripts/*.tsv",
            "scripts/check.sh",
            "scripts/audit-1170-gate-bypass.baseline.json",
        ),
    ),
    Stage(
        "cargo-test",
        "the host-runnable cargo tests and checks",
        ("crates/**/*", "Cargo.toml", "Cargo.lock", "build-support/**/*"),
    ),
    Stage(
        "cargo-build",
        "the cross-compiled product: does the committed state link, are the shells attested, "
        "are the DLLs byte-identical",
        (
            "crates/**/*",
            "Cargo.toml",
            "Cargo.lock",
            "vendor/**/*",
            "build-support/**/*",
            ".cargo/config.toml",
            "scripts/er-build-dlls.sh",
            "scripts/me3-dll-list.py",
        ),
    ),
)

STAGE_NAMES = tuple(s.name for s in STAGES)

# The rule for the 79 toolchain steps, applied in order, first match wins. Deliberately tiny: a new
# line here is a decision worth making explicitly rather than absorbing into a default.
#
# `cargo xwin` is named before the general `cargo` line because the two mean different things. A
# cross-compile is not a host-runnable check, and the stage it lands in is the stage whose CI job
# installs cargo-xwin and restores the gigabyte of Windows CRT and SDK behind it. Routed to
# `cargo-test` instead, a `cargo xwin` step runs in a job that has neither and dies on `no such
# subcommand`.
TOOLCHAIN_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^shellcheck\s"), "lint"),
    (re.compile(r"^rustfmt\s"), "lint"),
    (re.compile(r"^cargo\s+fmt\b"), "lint"),
    (re.compile(r"^(opa|cupcake)\s"), "policy"),
    (re.compile(r"^command -v cupcake\b"), "policy"),
    (re.compile(r"^cargo\s+(\+\S+\s+)?xwin\b"), "cargo-build"),
    (re.compile(r"^cargo\s"), "cargo-test"),
)


@dataclass
class StagedStep:
    line: int
    text: str
    key: str | None
    stage: str | None
    why: str  # "ledger" or the toolchain rule that matched, for --list


def read_stage_column(path: Path = LEDGER) -> dict[str, str]:
    """key -> stage, from the fifth column of the portability ledger.

    A row with only four columns has no stage yet; it is reported by `--check` rather than
    defaulted, because defaulting is how a step stops being classified silently.
    """
    stages: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").split("\n"):
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 4:
            continue
        if len(parts) >= 5 and parts[4].strip():
            stages[parts[2]] = parts[4].strip()
    return stages


def stage_for_toolchain(text: str) -> tuple[str, str] | tuple[None, str]:
    for pattern, stage in TOOLCHAIN_RULES:
        if pattern.match(text):
            return stage, f"rule {pattern.pattern}"
    return None, "no toolchain rule"


def staged_steps(check_sh: Path = CHECK_SH, ledger: Path = LEDGER) -> list[StagedStep]:
    portability = _load_portability()
    column = read_stage_column(ledger)
    out = []
    for step in portability.parse_steps(check_sh):
        if step.key is None:
            stage, why = stage_for_toolchain(step.text)
        else:
            stage, why = column.get(step.key), "ledger"
        out.append(StagedStep(line=step.line, text=step.text, key=step.key, stage=stage, why=why))
    return out


def check(check_sh: Path = CHECK_SH, ledger: Path = LEDGER) -> list[str]:
    """Every step is in exactly one stage, every stage has steps, every name is known."""
    steps = staged_steps(check_sh, ledger)
    problems = []
    for step in steps:
        if step.stage is None:
            if step.key is None:
                problems.append(
                    f"line {step.line}: no toolchain rule matches {step.text[:70]!r}. "
                    "Add one to TOOLCHAIN_RULES in scripts/check-stages.py, or make the step "
                    "invoke a repo script so it earns a ledger row."
                )
            else:
                problems.append(
                    f"line {step.line}: {step.key!r} has no stage. Add a fifth column to its row "
                    f"in {ledger.name}: one of {', '.join(STAGE_NAMES)}."
                )
        elif step.stage not in STAGE_NAMES:
            problems.append(
                f"line {step.line}: {step.key or step.text[:50]!r} names unknown stage "
                f"{step.stage!r}. Known stages: {', '.join(STAGE_NAMES)}."
            )
    occupied = {s.stage for s in steps}
    for name in STAGE_NAMES:
        if name not in occupied:
            problems.append(
                f"stage {name!r} is declared in scripts/check-stages.py and no step is in it. "
                "An empty stage is a job that always passes, which is worse than no job."
            )
    # A stage column on a row whose key is not a step is already caught by
    # `ci-gate-portability.py --check` as an orphan row, so it is not re-reported here. What that
    # gate cannot see is a stage name, which is why this loop exists at all.
    column = read_stage_column(ledger)
    keys = {s.key for s in steps if s.key}
    for key, name in sorted(column.items()):
        if key in keys and name not in STAGE_NAMES:
            continue
        if key not in keys:
            problems.append(f"ledger row {key!r} carries stage {name!r} but is not a step")
    return problems


def lines_in(stage: str, check_sh: Path = CHECK_SH, ledger: Path = LEDGER) -> list[int]:
    return [s.line for s in staged_steps(check_sh, ledger) if s.stage == stage]


def lines_outside(stage: str, check_sh: Path = CHECK_SH, ledger: Path = LEDGER) -> list[int]:
    return [s.line for s in staged_steps(check_sh, ledger) if s.stage != stage]


def stage_inputs(stage: str) -> tuple[str, ...]:
    """Every file pattern the named stage reads: its declared globs, plus its own gate scripts.

    The gate scripts are derived rather than declared because they are already written down --
    each is the command text of a step, and `staged_steps` says which stage that step is in.
    Requiring the table to repeat them would be a second list to fall behind the first, and it
    fell behind in exactly that way: `suite` runs `scripts/test-git-pre-push-block-main.sh` while
    declaring only `scripts/*.py`, so a diff consisting of that one test claimed no stage reads it.

    One set, two readers. `stage_input_digest` hashes it to key a cache; `stages_for_paths` matches
    a changed path against it to pick a stage. A stage cached against one set of files and selected
    against another would be the drift this file exists to refuse.
    """
    spec = next(s for s in STAGES if s.name == stage)
    return tuple(sorted(set(spec.inputs) | stage_gate_scripts(stage)))


def stage_input_digest(stage: str) -> str:
    """A content hash over everything the named stage reads, for an `actions/cache` key.

    Paths are sorted and both name and bytes are hashed, so a rename moves the digest even when no
    byte of content changed. Missing globs contribute nothing rather than raising: a checkout
    without `vendor/` is a real configuration, not an error.
    """
    digest = hashlib.sha256()
    seen: set[Path] = set()
    for pattern in stage_inputs(stage):
        for path in sorted(REPO.glob(pattern)):
            if path.is_dir() or not path.exists():
                continue
            rel = path.relative_to(REPO)
            if rel in seen or rel.parts[0] in {"target", ".git"}:
                continue
            seen.add(rel)
    for rel in sorted(seen):
        digest.update(str(rel).encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256((REPO / rel).read_bytes()).digest())
    return digest.hexdigest()


# --- which stages can a set of changed paths invalidate? --------------------------------------
# The same `inputs` globs, read the other way round. A digest asks "what did these files hash to";
# selection asks "is this changed path one of them". Both questions have to be answered from one
# declaration or a stage could be cached against one set of files and selected against another --
# which is the drift this whole file exists to refuse, wearing a different hat.


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """`Path.glob`'s matching, rewritten as a whole-path predicate.

    `PurePath.full_match` and `glob.translate` both say this in one call and both arrived in
    python 3.13. The runner this suite uses is `ubuntu-latest`, whose system python is 3.12 and
    which the workflow does not replace, so the translation is spelled out instead.

    `**` stands for zero or more whole components and carries its own trailing separator, so
    `docs/**/*` matches `docs/a.md` as well as `docs/plans/a.md` -- the same as `Path.glob`. A
    pattern ending in a bare `**` is not written in the table and would match nothing here.
    """
    parts = pattern.split("/")
    out: list[str] = []
    for index, part in enumerate(parts):
        if part == "**":
            out.append("(?:[^/]+/)*")
            continue
        out.append(_segment_regex(part))
        if index != len(parts) - 1:
            out.append("/")
    return re.compile("(?s:" + "".join(out) + r")\Z")


def _segment_regex(segment: str) -> str:
    """One path component: `*` and `?` stop at a separator, `[...]` passes through."""
    out: list[str] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            close = index + 1
            if close < len(segment) and segment[close] in "!^":
                close += 1
            if close < len(segment) and segment[close] == "]":
                close += 1
            while close < len(segment) and segment[close] != "]":
                close += 1
            if close >= len(segment):
                out.append(re.escape("["))
            else:
                body = segment[index + 1 : close]
                out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
                index = close
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


@dataclass(frozen=True)
class Selection:
    """Which stages a set of changed paths can invalidate, and why the others cannot."""

    stages: tuple[str, ...]
    reasons: dict[str, str]  # stage -> why it is in or out
    unmatched: tuple[str, ...]  # changed paths no stage claims
    select_all_reason: str | None


def stages_for_paths(paths: list[str]) -> Selection:
    """Every stage one of `paths` matches -- and every stage, whenever that is not provable.

    Three things select the whole suite, and each of them is an answer to a question this
    function cannot answer rather than a policy choice:

      * an empty path list. A push with no diff against the base is a push to the base, where a
        narrowed run would be the one run nothing else covers.
      * `ER_SCOPE_ALL=1`, the override `scripts/er-change-scope.py` already reads.
      * a changed path that matches no stage's globs. Nobody classified it, so nobody can say
        which gate reads it; the answer to an unclassified input is all of them.
    """
    matchers = {name: [glob_to_regex(p) for p in stage_inputs(name)] for name in STAGE_NAMES}
    hit: dict[str, set[str]] = {name: set() for name in STAGE_NAMES}
    unmatched: list[str] = []
    for path in paths:
        claimed = False
        for name, patterns in matchers.items():
            if any(pattern.match(path) for pattern in patterns):
                hit[name].add(path)
                claimed = True
        if not claimed:
            unmatched.append(path)

    select_all_reason: str | None = None
    if os.environ.get("ER_SCOPE_ALL") == "1":
        select_all_reason = "ER_SCOPE_ALL=1 in the environment"
    elif not paths:
        select_all_reason = "no changed path was given, so nothing is provably untouched"
    elif unmatched:
        select_all_reason = (
            "no stage declares "
            + ", ".join(sorted(unmatched)[:4])
            + (f" (+{len(unmatched) - 4} more)" if len(unmatched) > 4 else "")
            + " as an input, so which gate reads it is unknown"
        )

    reasons: dict[str, str] = {}
    for name in STAGE_NAMES:
        if select_all_reason is not None:
            reasons[name] = "every stage is selected: " + select_all_reason
        elif hit[name]:
            sample = sorted(hit[name])[:3]
            reasons[name] = f"reads {len(hit[name])} changed path(s): {', '.join(sample)}"
        else:
            spec = next(s for s in STAGES if s.name == name)
            reasons[name] = (
                "not selected for this push -- none of its inputs ("
                + ", ".join(spec.inputs)
                + ", plus its own gate scripts) match any changed path. It did not run, and that "
                "is not a pass for it."
            )
    if select_all_reason is not None:
        chosen = tuple(STAGE_NAMES)
    else:
        chosen = tuple(name for name in STAGE_NAMES if hit[name])
    return Selection(chosen, reasons, tuple(sorted(unmatched)), select_all_reason)


# --- are those globs honest about what the stage reads? ---------------------------------------
# Selection is only as good as the `inputs` declaration, and a declaration nothing measures is the
# hand-written list this file was created to abolish. Two readings of the tree keep it honest, and
# both are derived rather than listed:
#
#   1. A stage runs gate scripts. Those scripts are files, and editing one changes what the stage
#      does, so the stage's globs have to claim them. This caught a real hole: `suite` runs
#      `scripts/test-git-pre-push-block-main.sh` while declaring only `scripts/*.py`, so a push
#      whose whole diff was that test would have skipped the stage that runs it.
#   2. A gate script names the paths it reads. `scripts/er-change-scope.py --selftest` already
#      audits build scripts this way; the same literal scan over gate scripts says which repo
#      paths a stage looks at, and the owning stage's globs have to claim those too.
#
# Both readings are over-inclusive on purpose. A path named in a comment, or in a fixture list
# that happens to name a real file, widens a stage's inputs by one glob. Over-inclusion costs a
# stage run; under-inclusion costs a gate nobody notices did not run.

_LITERAL_PATH = re.compile(r"[A-Za-z0-9_.*?/\[\]-]+")

# The table's own globs, as strings. `check-stages.py` is itself a `suite` gate, so the scan below
# reads `STAGES` and reports `crates/**/*`, `docs/**/*` and `.cupcake/**/*` as paths the `suite`
# stage looks at. It does not: as a step, this file reads `check.sh` and the portability ledger and
# nothing else, and those globs are a declaration about ten other stages. Honouring them would make
# `suite` claim the whole repository and a rego-only push would run it for nothing.
_DECLARED_GLOBS = frozenset(pattern for spec in STAGES for pattern in spec.inputs)


def repo_top_level() -> frozenset[str]:
    """The repository's own top-level directory names, so the scan below needs no list."""
    skip = {".git", "target", "stage-results"}
    return frozenset(p.name for p in REPO.iterdir() if p.is_dir() and p.name not in skip)


@functools.lru_cache(maxsize=1)
def tracked_paths() -> frozenset[str]:
    """Every path `git` can name in a diff, asked once.

    A submodule appears here as its gitlink -- `third_party/ER-Save-File-Readers`, one entry --
    and never as an interior file, and an ignored path does not appear at all. That is the whole
    reason this exists: the audit below is about which stage a push must select, and a push diff
    can only ever name a path in this set.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return frozenset()
    return frozenset(entry for entry in out.split("\0") if entry)


def is_diffable(raw: str) -> bool:
    """Can a push diff name `raw`, or anything under it?

    The audit used to ask the filesystem instead, and the filesystem answers differently in
    different checkouts of the same commit: an ignored `vendor/` tree and a populated submodule
    exist in the main working tree and not in a `git worktree`, so the same commit came back
    green in one and red in the other. Measured 2026-09-14 -- `suite` reported
    `third_party/ER-Save-File-Readers/testdata/vagabond/save_slots/0.sl2` and `policy` reported
    `vendor/seamless-coop-v1.9.9/SeamlessCoop/ersc.dll`, blocking a push from a worktree over two
    files no commit has ever carried. Neither can appear in a diff, so declaring them would buy
    nothing; the filter is the fix, not a wider `inputs`.
    """
    tracked = tracked_paths()
    if raw in tracked:
        return True
    prefix = raw.rstrip("/") + "/"
    return any(entry.startswith(prefix) for entry in tracked)


def code_without_prose(text: str, suffix: str) -> str:
    """`text` with comments and docstrings blanked, so a path named in prose is not read as a read.

    `scripts/er-change-scope.py` already blanks the same spans for the same reason -- a comment is
    not a build input -- and it is the same scanner, `scripts/check-comment-caps.py`, doing it. A
    suffix that scanner has no dialect for comes back whole.
    """
    scanner = _load_comment_scanner()
    dialect = scanner.SCANNED_SUFFIXES.get(suffix)
    if dialect is None:
        return text
    prose = {line for line, _ in scanner.prose_spans(text, dialect)}
    return "\n".join("" if n in prose else body for n, body in enumerate(text.splitlines(), 1))


def _load_comment_scanner():
    path = REPO / "scripts" / "check-comment-caps.py"
    spec = importlib.util.spec_from_file_location("er_comment_caps_for_stages", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"check-stages: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def named_repo_paths(source: str, top_level: frozenset[str]) -> set[str]:
    """Repo-relative paths and globs a gate script names, keeping only the ones git can name.

    Resolving is what separates a read from a fixture: `crates/nope/src/does-not-exist.rs` and
    `docs/x.md` are invented by selftests and name nothing, while `docs/recon/` and
    `crates/er-hook/src/lib.rs` are files git tracks. A glob is kept when it matches at least one
    tracked file, which is the same test `stage_input_digest` applies narrowed by
    [`is_diffable`].

    Tracked rather than merely present on disk, because the audit this feeds is about which stage
    a push must select and the two answers differ per checkout -- see `is_diffable`.
    """
    found: set[str] = set()
    for match in _LITERAL_PATH.finditer(source):
        raw = match.group(0).rstrip("/")
        head = raw.split("/", 1)[0]
        if head not in top_level or "/" not in raw or raw in _DECLARED_GLOBS:
            continue
        if any(char in raw for char in "*?["):
            try:
                hits = [f for f in REPO.glob(raw) if f.is_file()]
            except (ValueError, OSError, IndexError):
                continue
            if any(is_diffable(f.relative_to(REPO).as_posix()) for f in hits):
                found.add(raw)
            continue
        if not is_diffable(raw):
            continue
        target = REPO / raw
        if target.is_file():
            found.add(raw)
        elif target.is_dir():
            found.add(raw + "/**/*")
    return found


_STEP_CACHE: list[StagedStep] | None = None


def stage_gate_scripts(stage: str, steps: list[StagedStep] | None = None) -> set[str]:
    """`scripts/<name>` for every gate script this stage's steps invoke.

    The step list is parsed once per process. `stage_inputs` asks for it once per stage and the
    selection asks `stage_inputs` once per stage, so re-parsing `check.sh` and the ledger each
    time would read both files a hundred times to get the same answer.
    """
    global _STEP_CACHE
    if steps is None and _STEP_CACHE is None:
        _STEP_CACHE = staged_steps()
    rows = steps if steps is not None else _STEP_CACHE
    assert rows is not None
    return {
        f"scripts/{s.key.split()[0]}"
        for s in rows
        if s.stage == stage and s.key and (REPO / "scripts" / s.key.split()[0]).is_file()
    }


def coverage_problems(stages: tuple[Stage, ...] = STAGES) -> list[str]:
    """Every path a stage demonstrably reads that its own `inputs` globs do not claim."""
    steps = staged_steps()
    top_level = repo_top_level()
    problems: list[str] = []
    for spec in stages:
        matchers = [glob_to_regex(p) for p in stage_inputs(spec.name)]

        def claimed(path: str, matchers: list[re.Pattern[str]] = matchers) -> bool:
            """Does this stage declare `path`, which may itself be a glob?

            A glob is claimed when every file it names is claimed. Comparing the two patterns as
            strings instead would report `crates/**/*.rs` as unclaimed against a declared
            `crates/er-gfx/**/*`, and expanding both sides is the only comparison that is about
            the files rather than about the spelling.
            """
            if not any(char in path for char in "*?["):
                return any(m.match(path) for m in matchers)
            for found in REPO.glob(path):
                if found.is_dir():
                    continue
                rel = found.relative_to(REPO).as_posix()
                if not any(m.match(rel) for m in matchers):
                    return False
            return True

        owed: set[str] = set()
        for script in sorted(stage_gate_scripts(spec.name, steps)):
            if not claimed(script):
                owed.add(script)
            source = (REPO / script).read_text(encoding="utf-8", errors="replace")
            source = code_without_prose(source, Path(script).suffix)
            owed.update(p for p in named_repo_paths(source, top_level) if not claimed(p))
        for path in sorted(owed):
            problems.append(
                f"stage {spec.name!r} reads {path!r} and does not declare it. Widen that stage's "
                "`inputs` in scripts/check-stages.py, or the pre-push selection will skip the "
                "stage on a push that changes it."
            )
    return problems


# What an INVOCATION of each tool looks like inside a gate script, as opposed to a mention of it.
# The distinction earns its keep on `xwin`: the pinned Windows CRT and SDK are about a gigabyte and
# several gates merely name the target triple in a comment. A bare-word scan marked seven of the
# eleven stages as needing that download; requiring an invocation marks six, and only
# `cargo-build` spells `cargo xwin` in check.sh itself.
INDIRECT_TOOL_USE = {
    "cargo": re.compile(r"\bcargo\s+(\+\S+\s+)?(xwin|build|check|test|fmt|clippy|metadata|tree)\b"),
    "xwin": re.compile(r"\bcargo\s+(\+\S+\s+)?xwin\b|--target[= ]x86_64-pc-windows-msvc"),
    "shellcheck": re.compile(r"\bshellcheck\s+[-\"$\w/]"),
    "rustfmt": re.compile(r"\brustfmt\s+[-\"$\w/]"),
    "opa": re.compile(r"\bopa\s+(test|eval|build|version|fmt|check)\b"),
    "cupcake": re.compile(r"\bcupcake\s+(eval|validate|--|version)\b"),
    "uv": re.compile(r"\buv\s+run\b"),
}


def stage_tools(stage: str) -> dict[str, bool]:
    """Which external tools a stage needs, so a CI job installs those and not the other seven.

    Derived from two readings, both over-inclusive on purpose. The first word of a toolchain step
    is the obvious one (`shellcheck ...` needs shellcheck). The second is the one that matters:
    most tool use in this suite is indirect -- `test-cupcake-policies.py` is a python3 step that
    spawns `cupcake eval` 176 times, and `check-rust-build.sh` is a bash step that runs
    `cargo xwin build`. Reading each gate script's own source for the binary's name catches those;
    a hand-written map of stage to tools would not, and would go stale the first time a gate
    learned a new dependency.

    Over-detection costs an install that was not needed. Under-detection costs coverage, and in
    two of these cases it costs it quietly -- check.sh reports a missing shellcheck or opa as
    `SKIPPED`, which is green. That asymmetry is why every pattern below is written loose.
    """
    portability = _load_portability()
    steps = [s for s in staged_steps() if s.stage == stage]
    tools = {k: False for k in ("cargo", "xwin", "shellcheck", "rustfmt", "opa", "cupcake", "uv")}
    ledger = portability.read_ledger()
    for step in steps:
        head = step.text.split()[0]
        if head in tools:
            tools[head] = True
        # A step's own text, not just the gate scripts it invokes. The target triple catches the
        # steps that name it; `cargo xwin` catches the ones that do not, because check.sh wraps a
        # long invocation and the triple can sit on a continuation line this text never sees.
        if "x86_64-pc-windows-msvc" in step.text or INDIRECT_TOOL_USE["xwin"].search(step.text):
            tools["xwin"] = True
        if step.key is None:
            continue
        row = ledger.get(step.key)
        if row and any(d.lstrip("!") == "uv" for d in row[1]):
            tools["uv"] = True
        script = REPO / "scripts" / step.key.split()[0]
        if not script.exists():
            continue
        source = script.read_text(encoding="utf-8", errors="replace")
        for name, pattern in INDIRECT_TOOL_USE.items():
            if pattern.search(source):
                tools[name] = True
    if tools["xwin"]:
        tools["cargo"] = True
    return tools


# --- positive controls ----------------------------------------------------------------------
# A classification gate that cannot catch its own drift is decoration, so each control plants a
# real defect in a synthetic pair of files and requires `--check` to name it. `--mode regex` in
# scripts/audit-selftest-vacuity.py lobotomises matchers; these cases are written so that a
# lobotomised matcher stops finding the planted defect.

_FIXTURE_CHECK_SH = """#!/usr/bin/env bash
_check_step_pattern='^[[:space:]]*(python3|bash|cargo|shellcheck|rustfmt|cupcake|opa|command -v)[[:space:]]'
python3 "$repo_root/scripts/alpha.py" --selftest
python3 "$repo_root/scripts/beta.py"
shellcheck "$repo_root/scripts/gamma.sh"
"""


def _fixture(tmp: Path, ledger_rows: list[str], check_sh: str = _FIXTURE_CHECK_SH):
    sh = tmp / "check.sh"
    sh.write_text(check_sh, encoding="utf-8")
    led = tmp / "ledger.tsv"
    led.write_text("# header\n" + "\n".join(ledger_rows) + "\n", encoding="utf-8")
    return sh, led


def selftest() -> int:
    cases: list[tuple[str, list[str], str, str]] = [
        (
            "a step whose ledger row has no stage column is named",
            [
                "portable\t-\talpha.py --selftest\tnote",
                "portable\t-\tbeta.py\tnote\tsuite",
            ],
            "fail",
            "has no stage",
        ),
        (
            "a stage name that is not declared is named",
            [
                "portable\t-\talpha.py --selftest\tnote\tsuite",
                "portable\t-\tbeta.py\tnote\tnot-a-real-stage",
            ],
            "fail",
            "unknown stage",
        ),
        (
            "a fully staged fixture passes",
            [
                "portable\t-\talpha.py --selftest\tnote\tsuite",
                "portable\t-\tbeta.py\tnote\tsuite",
            ],
            "pass",
            "",
        ),
    ]
    failures = 0
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        for label, rows, expect, needle in cases:
            sh, led = _fixture(tmp, rows)
            # The fixture declares only `suite` and `lint`, so every other declared stage would be
            # reported empty. Those reports are filtered out here rather than by weakening
            # `check`: the empty-stage rule is itself covered by the last case below.
            problems = [
                p for p in check(sh, led) if "and no step is in it" not in p
            ]
            got = "fail" if problems else "pass"
            ok = got == expect and (not needle or any(needle in p for p in problems))
            print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
            if not ok:
                failures += 1
                for problem in problems:
                    print(f"          {problem}")
    # The empty-stage rule, on the real tree: every declared stage must hold at least one step.
    occupied = {s.stage for s in staged_steps()}
    empty = [n for n in STAGE_NAMES if n not in occupied]
    ok = not empty
    print(f"  {'ok  ' if ok else 'FAIL'}  every declared stage holds at least one real step")
    if not ok:
        failures += 1
        print(f"          empty: {', '.join(empty)}")
    # And the toolchain rules really are total over the real tree's tool steps.
    unmatched = [s for s in staged_steps() if s.key is None and s.stage is None]
    ok = not unmatched
    print(f"  {'ok  ' if ok else 'FAIL'}  every toolchain step matches a rule")
    if not ok:
        failures += 1
        for step in unmatched:
            print(f"          line {step.line}: {step.text[:80]}")
    failures += selection_selftest()
    print(f"check-stages selftest: {failures} failure(s)")
    return 1 if failures else 0


# The synthetic stage table the selection cases run against. Synthetic on purpose: a case written
# against the real table would pass or fail for whatever the table happens to say this week, and
# the property under test is the matcher, not the repository.
_FIXTURE_STAGES: tuple[Stage, ...] = (
    Stage("paper", "documents", ("docs/**/*", "*.md")),
    Stage("rules", "the rulebook", (".cupcake/**/*",)),
    Stage("build", "the compiler", ("crates/**/*", "Cargo.toml")),
)


def selection_selftest() -> int:
    """Positive controls for `stages_for_paths` and the glob matcher underneath it."""
    failures = 0

    def case(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"  {'ok  ' if condition else 'FAIL'}  {label}")
        if not condition:
            failures += 1
            if detail:
                print(f"          {detail}")

    # --- the matcher, against `Path.glob`'s own answers --------------------------------------
    matches = [
        ("docs/**/*", "docs/a.md", True),
        ("docs/**/*", "docs/plans/deep/a.md", True),
        ("docs/**/*", "crates/a.rs", False),
        ("**/*.rs", "a.rs", True),
        ("**/*.rs", "crates/er-gfx/src/lib.rs", True),
        ("**/*.rs", "crates/er-gfx/src/lib.py", False),
        ("crates/**/*.rs", "crates/x/src/a.rs", True),
        ("crates/**/*.rs", "docs/x/a.rs", False),
        ("scripts/hooks/*", "scripts/hooks/pre-push", True),
        ("scripts/hooks/*", "scripts/hooks/nested/pre-push", False),
        ("scripts/*.py", "scripts/a.py", True),
        ("scripts/*.py", "scripts/sub/a.py", False),
        ("Cargo.toml", "Cargo.toml", True),
        ("Cargo.toml", "crates/x/Cargo.toml", False),
    ]
    wrong = [
        (pattern, path)
        for pattern, path, want in matches
        if bool(glob_to_regex(pattern).match(path)) is not want
    ]
    case(f"the glob matcher agrees with Path.glob on {len(matches)} shapes", not wrong, str(wrong))

    # ...and it agrees with the real thing rather than with this file's idea of it: every file
    # `Path.glob` returns for a declared pattern must match that pattern's regex.
    disagreements = []
    for spec in STAGES:
        for pattern in spec.inputs:
            regex = glob_to_regex(pattern)
            for found in REPO.glob(pattern):
                if found.is_dir():
                    continue
                rel = found.relative_to(REPO).as_posix()
                if not regex.match(rel):
                    disagreements.append((pattern, rel))
    case(
        "every file Path.glob returns for a declared input matches that input's regex",
        not disagreements,
        str(disagreements[:4]),
    )

    # --- selection over the synthetic table ---------------------------------------------------
    real, globals()["STAGES"] = STAGES, _FIXTURE_STAGES
    real_names, globals()["STAGE_NAMES"] = STAGE_NAMES, tuple(s.name for s in _FIXTURE_STAGES)
    real_declared = globals()["_DECLARED_GLOBS"]
    globals()["_DECLARED_GLOBS"] = frozenset(p for s in _FIXTURE_STAGES for p in s.inputs)
    scope_all, os.environ["ER_SCOPE_ALL"] = os.environ.pop("ER_SCOPE_ALL", None), "0"
    try:
        docs_only = stages_for_paths(["docs/plans/a.md", "README.md"])
        case(
            "a documentation-only path list selects the document stage and not the compiler",
            docs_only.stages == ("paper",),
            str(docs_only.stages),
        )
        rules_only = stages_for_paths([".cupcake/policies/claude/a.rego"])
        case(
            "a .cupcake-only path list selects the rulebook stage alone",
            rules_only.stages == ("rules",),
            str(rules_only.stages),
        )
        empty = stages_for_paths([])
        case(
            "an empty path list selects every stage, and says why",
            empty.stages == tuple(s.name for s in _FIXTURE_STAGES)
            and "no changed path" in (empty.select_all_reason or ""),
            str(empty.stages) + " " + str(empty.select_all_reason),
        )
        stray = stages_for_paths(["some/unclassified/thing.bin"])
        case(
            "a path no stage declares selects every stage rather than none",
            stray.stages == tuple(s.name for s in _FIXTURE_STAGES)
            and stray.unmatched == ("some/unclassified/thing.bin",),
            str(stray.stages),
        )
        mixed = stages_for_paths(["docs/a.md", "crates/x/src/a.rs"])
        case(
            "a mixed list selects the union, not the intersection",
            set(mixed.stages) == {"paper", "build"},
            str(mixed.stages),
        )
        skipped = mixed.reasons["rules"]
        case(
            "an unselected stage says it did not run and that this is not a pass",
            "not selected" in skipped and "not a pass" in skipped,
            skipped,
        )
        os.environ["ER_SCOPE_ALL"] = "1"
        forced = stages_for_paths(["docs/a.md"])
        case(
            "ER_SCOPE_ALL=1 selects every stage",
            forced.stages == tuple(s.name for s in _FIXTURE_STAGES),
            str(forced.stages),
        )
    finally:
        globals()["STAGES"], globals()["STAGE_NAMES"] = real, real_names
        globals()["_DECLARED_GLOBS"] = real_declared
        os.environ.pop("ER_SCOPE_ALL", None)
        if scope_all is not None:
            os.environ["ER_SCOPE_ALL"] = scope_all

    # --- and the real table declares what its own gates read ---------------------------------
    problems = coverage_problems()
    case(
        "every path a stage's gates read is declared by that stage",
        not problems,
        "; ".join(problems[:3]),
    )

    # --- ...asked of git, so the answer does not depend on which checkout is asking -----------
    case(
        "a tracked file is diffable and an invented one is not",
        is_diffable("scripts/check-stages.py") and not is_diffable("scripts/nope-xyz.py"),
        f"tracked={is_diffable('scripts/check-stages.py')} "
        f"invented={is_diffable('scripts/nope-xyz.py')}",
    )
    gitlinks = [
        entry.split("\t", 1)[1]
        for entry in subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "--stage"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        ).stdout.splitlines()
        if entry.startswith("160000 ")
    ]
    # A submodule is one gitlink in the index and its interior is not in the index at all, so a
    # push diff names the gitlink and never a file under it. Deriving the name from git rather
    # than typing one keeps the control alive when the submodule list changes; with none checked
    # out there is nothing to control and the case says so rather than passing on an empty set.
    case(
        "a submodule's interior is not diffable, only its gitlink",
        bool(gitlinks)
        and all(is_diffable(link) for link in gitlinks)
        and not any(is_diffable(f"{link}/README.md") for link in gitlinks),
        f"gitlinks={gitlinks[:3]}",
    )
    return failures


def changed_paths_for(base_ref: str, revs: list[str]) -> list[str]:
    """The changed paths this selection is about, through `scripts/er-dll-closure.py`.

    That module is where `resolve_base` and `changed_paths` live, and
    `scripts/er-change-scope.py` imports them from it for the same reason: a second walk over
    the same diff is the drift this repo keeps closing. `revs` asks about the commits being
    pushed rather than the working tree, which is the question a pre-push hook has -- a dirty
    tree is not what reaches origin.
    """
    path = REPO / "scripts" / "er-dll-closure.py"
    spec = importlib.util.spec_from_file_location("er_dll_closure_for_stages", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    closure = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = closure
    spec.loader.exec_module(closure)
    # Never fetched. A stale base widens the diff, which selects more stages, which is the safe
    # direction; a network round trip on every push is a cost nobody asked for.
    merge_base, _ = closure.resolve_base(base_ref, False)
    if not revs:
        return closure.changed_paths(merge_base)
    seen: set[str] = set()
    for rev in revs:
        rev_base = closure.git("merge-base", base_ref, rev).strip()
        seen.update(
            line.strip()
            for line in closure.git("diff", "--name-only", rev_base, rev).splitlines()
            if line.strip()
        )
    return sorted(seen)


def _emit_selection(args) -> int:
    """Selected stage names on stdout; the whole accounting, skips included, on stderr.

    Exit is 0 whatever happens, and that is the fail-open contract rather than laziness: the
    caller runs the names it is given, so the way to fail open is to name every stage. A
    non-zero exit would abort a `set -e` hook instead, which selects nothing at all --
    the one outcome worse than running everything.
    """
    paths: list[str]
    failure: str | None = None
    if args.stages_for_diff:
        try:
            paths = changed_paths_for(args.base, args.revs)
        except Exception as err:  # noqa: BLE001 -- every failure has one answer: every stage
            failure = f"cannot resolve the diff ({type(err).__name__}: {err})"
            paths = []
    elif args.stages_for_paths:
        paths = [p.strip() for p in args.stages_for_paths if p.strip()]
    else:
        paths = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]

    try:
        selection = stages_for_paths(paths)
    except Exception as err:  # noqa: BLE001
        failure = failure or f"selection raised ({type(err).__name__}: {err})"
        selection = Selection(tuple(STAGE_NAMES), {}, (), failure)

    if failure:
        print(f"check-stages: {failure}.", file=sys.stderr)
        print("check-stages: failing open -- every stage is selected.", file=sys.stderr)
        selection = Selection(
            tuple(STAGE_NAMES),
            {name: "every stage is selected: " + failure for name in STAGE_NAMES},
            (),
            failure,
        )

    print("\n".join(selection.stages))
    print(
        f"check-stages: {len(selection.stages)} of {len(STAGE_NAMES)} stage(s) selected from "
        f"{len(paths)} changed path(s).",
        file=sys.stderr,
    )
    if selection.select_all_reason:
        print(f"check-stages: {selection.select_all_reason}", file=sys.stderr)
    for name in STAGE_NAMES:
        mark = "SELECTED    " if name in selection.stages else "not selected"
        print(f"  {mark}  {name:<14} {selection.reasons.get(name, '')}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="the parity gate")
    ap.add_argument("--list", action="store_true", help="every step with its stage")
    ap.add_argument("--stages", action="store_true", help="stage names, one per line")
    ap.add_argument("--matrix", action="store_true", help="stage names as JSON for the CI matrix")
    ap.add_argument(
        "--only",
        metavar="CSV",
        default="",
        help="restrict --matrix to these stage names. An empty value means all of them, so a "
        "caller can pass a workflow input straight through without branching on it.",
    )
    ap.add_argument("--lines", metavar="STAGE", help="check.sh line numbers in one stage")
    ap.add_argument("--skip-lines", metavar="STAGE", help="line numbers NOT in one stage")
    ap.add_argument("--inputs", metavar="STAGE", help="content digest of that stage's inputs")
    ap.add_argument(
        "--tools",
        metavar="STAGE",
        help="which external tools that stage needs, as KEY=0/1 lines for $GITHUB_OUTPUT",
    )
    ap.add_argument(
        "--result-stub",
        metavar="STAGE",
        help="a stage result file for a stage that did not execute, so the combined report can "
        "still give every step of it a state",
    )
    ap.add_argument(
        "--state",
        default="CACHED",
        help="the state --result-stub gives that stage's own steps (default CACHED)",
    )
    ap.add_argument(
        "--reason", default="", help="the reason --result-stub records beside that state"
    )
    ap.add_argument(
        "--stage-of",
        metavar="PREFIX",
        help="the stage owning the step whose text starts with PREFIX, for callers that must "
        "name a stage without hard-coding one",
    )
    ap.add_argument(
        "--stages-for-paths",
        nargs="*",
        metavar="PATH",
        help="the stages these repo-relative paths can invalidate, one name per line. With no "
        "paths, they are read from stdin, one per line.",
    )
    ap.add_argument(
        "--stages-for-diff",
        action="store_true",
        help="the same, over the diff against --base (or the commits named by --rev)",
    )
    ap.add_argument("--base", default="origin/main", help="the diff base for --stages-for-diff")
    ap.add_argument(
        "--rev",
        dest="revs",
        action="append",
        default=[],
        metavar="SHA",
        help="diff this commit against the base instead of the working tree (repeatable), which "
        "is the shape a pre-push hook needs: what reaches origin, not what is in the tree",
    )
    ap.add_argument(
        "--audit-inputs",
        action="store_true",
        help="every path a stage's gates demonstrably read that its `inputs` do not declare",
    )
    ap.add_argument("--steps-tsv", action="store_true", help="line<TAB>text for every step")
    ap.add_argument("--counts", action="store_true", help="how many steps per stage")
    ap.add_argument("--selftest", action="store_true", help="positive controls for --check")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.steps_tsv:
        portability = _load_portability()
        for step in portability.parse_steps():
            print(f"{step.line}\t{step.text}")
        return 0

    if args.audit_inputs:
        problems = coverage_problems()
        for problem in problems:
            print(f"check-stages: {problem}")
        print(f"check-stages: {len(problems)} undeclared stage input(s)")
        return 1 if problems else 0

    if args.stages_for_paths is not None or args.stages_for_diff:
        return _emit_selection(args)

    if args.stages:
        print("\n".join(STAGE_NAMES))
        return 0

    if args.matrix:
        # The intersection lives here rather than in shell inside .github/workflows/check.yml,
        # where it was ten lines of python in a heredoc nested in a YAML block scalar -- correct
        # only for as long as nobody re-indents the block, since a `<<PY` terminator has to land in
        # column 1 and the block scalar is what puts it there. An unknown name is refused rather
        # than dropped: a typo that silently narrows the matrix is a stage nobody notices is gone.
        names = list(STAGE_NAMES)
        if args.only:
            want = [w.strip() for w in args.only.split(",") if w.strip()]
            unknown = [w for w in want if w not in names]
            if unknown:
                print(
                    f"check-stages: no such stage(s): {unknown}; have {names}", file=sys.stderr
                )
                return 2
            names = [n for n in names if n in want]
        print(json.dumps(names))
        return 0

    if args.lines:
        print("\n".join(str(n) for n in lines_in(args.lines)))
        return 0

    if args.skip_lines:
        if args.skip_lines not in STAGE_NAMES:
            print(f"check-stages: unknown stage {args.skip_lines!r}", file=sys.stderr)
            return 2
        print("\n".join(str(n) for n in lines_outside(args.skip_lines)))
        return 0

    if args.tools:
        if args.tools not in STAGE_NAMES:
            print(f"check-stages: unknown stage {args.tools!r}", file=sys.stderr)
            return 2
        for key, value in sorted(stage_tools(args.tools).items()):
            print(f"{key}={1 if value else 0}")
        return 0

    if args.result_stub:
        # A stage that was skipped still owes the combined report a state for every one of its
        # steps. Emitting that here rather than in shell keeps one definition of the file format:
        # check.sh writes the same columns from `_check_summary`.
        if args.result_stub not in STAGE_NAMES:
            print(f"check-stages: unknown stage {args.result_stub!r}", file=sys.stderr)
            return 2
        print(f"# stage\t{args.result_stub}")
        print("# seconds\t0")
        print("# reached_end\t1")
        for step in staged_steps():
            own = step.stage == args.result_stub
            state = args.state if own else "OTHER_STAGE"
            print(f"{step.line}\t{state}\t{args.reason if own else ''}\t{step.text}")
        return 0

    if args.stage_of:
        # Used by check.sh to ask which stage owns the cupcake steps, so its fail-fast guard can
        # stand down in the other stages without a stage name written into check.sh. Ambiguity is
        # an error rather than a first match: two stages behind one prefix means the caller is
        # about to act on a guess.
        owners = {s.stage for s in staged_steps() if s.text.startswith(args.stage_of)}
        if not owners:
            print(f"check-stages: no step starts with {args.stage_of!r}", file=sys.stderr)
            return 2
        if len(owners) > 1:
            print(
                f"check-stages: {args.stage_of!r} spans stages {sorted(owners)}", file=sys.stderr
            )
            return 2
        print(owners.pop())
        return 0

    if args.inputs:
        if args.inputs not in STAGE_NAMES:
            print(f"check-stages: unknown stage {args.inputs!r}", file=sys.stderr)
            return 2
        print(stage_input_digest(args.inputs))
        return 0

    if args.counts:
        steps = staged_steps()
        for name in STAGE_NAMES:
            n = sum(1 for s in steps if s.stage == name)
            print(f"{n:4d}  {name}")
        unstaged = sum(1 for s in steps if s.stage is None)
        print(f"{unstaged:4d}  (no stage)")
        print(f"{len(steps):4d}  total")
        return 0

    if args.list:
        for step in staged_steps():
            print(f"{step.stage or '-':<14} line {step.line:<5d} {step.text[:100]}")
        return 0

    if args.check:
        problems = check()
        for problem in problems:
            print(f"check-stages: {problem}")
        if problems:
            print(f"check-stages: {len(problems)} problem(s)")
            return 1
        steps = staged_steps()
        print(
            f"check-stages: {len(steps)} step(s) across {len(STAGE_NAMES)} stage(s); "
            "each step in exactly one, each stage occupied"
        )
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
