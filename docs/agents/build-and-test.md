# Build, test and architecture

## Build & Test

This repo must be a sibling of a `fromsoftware-rs` checkout (the root crate uses `../fromsoftware-rs` path dependencies).

```bash
# Full quality gate: lossy-UTF8 lint, cargo fmt --all -- --check,
# and a windows-target cargo check (cross-compiled from Linux via cargo-xwin).
#
# ORCHESTRATOR-ONLY, IN THE MAIN TREE. check.sh fails closed inside an agent
# worktree -- `repo_root == */.claude/worktrees/agent-*` exits 2 with
# "REFUSED -- this is an agent worktree" before the summary trap installs -- and
# it refuses a second concurrent run anywhere. So do NOT write `bash
# scripts/check.sh` into a subagent brief when that agent has worktree
# isolation: the instruction is unrunnable, and an agent that reaches for the
# ER_CHECK_FORCE=1 override burns the ~30 minutes of sleep-polling the guard
# exists to prevent (bd subagent-full-check-sh-sleep-poll-is-the-hour-long-tax-2026-09-02,
# bd never-tell-a-worktree-subagent-to-run-check-sh-2026-09-10).
#
# A subagent never lints, formats or commits (user directive 2026-10-17; refused by
# `no_lint_before_commit` and `git_commit_authorization`). Give a worktree subagent
# tests only, naming its crates and the checks its edit actually touches:
#   cargo test -p <crate>
#   python3 scripts/check-shared-hook-rvas.py      # whenever a detour moves
#   python3 scripts/check-me3-dll-conflicts.py     # whenever the conflict table moves
#   python3 scripts/check-stages.py --selftest     # whenever a scripts/*.py gate is added
#   python3 scripts/check-comment-caps.py --selftest   # selftests are tests, not lints
# The lints -- `cargo clippy -p <crate> --all-targets` (`cargo test` never invokes clippy,
# and the workspace denies warnings), `cargo fmt -p <crate> -- --check`,
# `python3 scripts/check-comment-caps.py <files>`, `python3 scripts/check-no-lossy-utf8.py`
# -- are the main session's, run only after the user replies `authorize commit` and
# before the commit that reply pays for.
# THE MODE MATTERS AS MUCH AS THE NAME. check.sh runs most gates twice -- `--selftest` first,
# then the gate itself -- and the selftest is the half that proves the gate can still fail.
# Running only the plain form passes while a gate is broken. Both halves of PR #456's red CI
# were gates that had been run in the wrong mode: `check-stages.py --check` instead of
# `--selftest` (which knows every path a stage reads must be declared by that stage), and
# `cargo test` instead of `cargo clippy` (which denied a `max().min()` as a clamp pattern).
# The whole-workspace verdict is the orchestrator's, run once at integration.
bash scripts/check.sh

# ...which is now ELEVEN STAGES fanned out as child processes, not one long process. The step
# list is unchanged and still lives in check.sh alone; what changed is that each step belongs to
# a named stage, so a failure lands somewhere named and the fast stages answer first. Measured on
# this tree: `lint` is 14s and `policy` 104s, against 887s for the whole suite run one step at
# a time. The fan-out at four jobs does the same work in 614s, and the number that really moved
# is time-to-first-failure: a formatting mistake is red in 14s instead of after 121s of
# unrelated gates.
bash scripts/check.sh --list-stages         # the stages, and how many steps each holds
bash scripts/check.sh --stage lint          # one stage, in this process, no children
bash scripts/check.sh --jobs 1              # every stage, one at a time
ER_CHECK_STAGES=lint,policy bash scripts/check.sh   # a deliberate subset (NOT a push shortcut)

# A stage takes a SHARED lock and a whole-suite run an EXCLUSIVE one, so two stage runs coexist
# and a whole-suite run excludes everything. The stage of every step comes from the fifth column
# of docs/ci-gate-portability.tsv (gate steps) or from seven rules in scripts/check-stages.py
# (toolchain steps); `check-stages.py --check` is a step of the suite and refuses a step that
# belongs to no stage or to two. .github/workflows/check.yml generates its matrix from the same
# command, so a GitHub run page shows the same eleven names.
#
# Same suite under act, in containers: bash scripts/act-check.sh --stage lint

# Host-buildable workspace members (no game dependencies):
cargo test -p er-soulsformats -p er-param-inspect
cargo check -p er-soulsformats -p er-param-inspect

# The game DLL itself (cross-compiled to x86_64-pc-windows-msvc from Linux via cargo-xwin):
cargo xwin build --release --target x86_64-pc-windows-msvc
# Output: target/x86_64-pc-windows-msvc/release/er_quickload.dll

# ...but that builds ONLY er-quickload. The workspace sets
#     default-members = ["crates/er-quickload"]
# so the bare command above silently skips EVERY other DLL crate -- er-invasion-warp,
# er-loading-portrait, er-save-picker, and the rest. It exits 0 in a fraction of a
# second having compiled nothing, which reads exactly like a successful incremental build.
# For any other DLL, name it:
cargo xwin build --release --target x86_64-pc-windows-msvc -p er-invasion-warp
```

**Check the output hash before staging or launching.** A build that "succeeded" without
recompiling leaves the previous DLL in place, and a runtime run against it produces evidence for
code that is not the code under test -- a failure mode indistinguishable from the feature not
working. `sha256sum target/x86_64-pc-windows-msvc/release/<name>.dll` before and after is enough.

**Prefer `scripts/er-build-dlls.sh <package>...` (or `--all`) over a bare `cargo xwin build`
whenever the DLL is going to be RUN.** It is the same cargo invocation with the `-p` flags filled
in from `scripts/me3-dll-list.py`, plus one thing bare cargo cannot do afterwards: it records
`<artifact>.provenance.json`, a content hash over the package's compiled dependency closure taken
while that tree was the one being compiled. Every launch script gates on that record
(`scripts/er-dll-freshness.sh` -> `scripts/er-dll-provenance.py verify`) and REFUSES rather than
launch an artifact it cannot tie to this tree. A DLL produced by a bare `cargo xwin build` carries
no such record, so those scripts will refuse it -- correctly, since nothing proves what it is.
`scripts/check-rust-build.sh` re-attests all 26 shells after its own relink for the same reason.

## Architecture Overview

- `crates/er-quickload/src/lib.rs` -- the injectable DLL. On `DLL_PROCESS_ATTACH` it spawns a recurring game task (via `CSTaskImp`) that watches the local player's TimeAct animation queue and applies the selected SpEffects, runtime probes, and native title/load instrumentation.
- `data/effects.json` -- the named SpEffect call list, embedded into the DLL at compile time and validated offline against `SpEffectParam`.
- `crates/soulsformats` (`er-soulsformats`) -- host-side library that drives a generated .NET "bridge" project against Smithbox's `Andre.Formats`/SoulsFormats to read `regulation.bin` params. Also contains the parser for FastSpEffectRecon Ghidra output (`recon` module).
- `tools/er-param-inspect` -- CLI over `er-soulsformats`: inspect param rows and validate `data/effects.json` against a regulation file.
- `docs/` -- reference-tree research notes and recon data (`docs/recon/`).
