#!/usr/bin/env bash
# Cupcake signal: runtime_evidence_for_head
#
# Consumed by:
#   * git_require_runtime_evidence (PreToolUse/Bash): refuses `git push` when the commits being
#     pushed change code that runs inside ELDEN RING and no run has produced evidence since.
#
# Why this exists
#
# 2026-09-09: two commits were authored and a push attempted for both -- an F3 key that had never
# been pressed in the game, and a stall-watchdog fix whose code had never executed, because the DLL
# in the running process had been built before either. The user stopped the push and asked for this
# guard by name. AGENTS.md already says to commit only after a runtime validation run completes;
# that rule is prose, and prose did not stop it. This does.
#
# What it emits, one line, one word:
#
#   `OK`         a run executed this code: a DLL log names the tip on its own `build git=` line,
#                or names a commit the tip adds no cargo work on top of.
#   `MISSING`    no run did. This is the case that denies.
#   `NOTRUNTIME` the commits about to be pushed touch no crate that ships in a DLL, so there is
#                nothing for a run to prove. Never denies.
#   `UNKNOWN`    the signal could not measure (no git, no log directory, unreadable, too slow), or
#                the command is not a push. Never denies: a guard that cannot see must not invent
#                a verdict, and the pre-push hook plus CI still stand behind it.
#
# One word with no fields, because the parsing is easier to selftest in bash than in rego, and the
# prose belongs to the sibling signal `runtime_evidence_note`, which the policy interpolates but
# never inspects. The header used to justify this differently -- that every parsing form tried in
# rego was inert -- and that was wrong: the policy was dead because of `sprintf`, which cupcake's
# WASM runtime does not implement, not because of anything to do with parsing or `input.signals`.
#
# Which logs count, and what a `+dirty` line means, is decided in one place for all three readers
# of this evidence: `scripts/er-runtime-evidence.py`. It reads the run root and the game directory
# both -- a run launched through `~/Elden/launch.sh` writes only into the second, and reading only
# the first refused a push on 2026-09-11 that a live run had proven. Its header carries the rest.
#
# Which repository is being pushed is decided by `scripts/cupcake_push_target_repo.py`, and it is
# not always the one this process starts in. Measured 2026-09-13: a session working in the main
# checkout ran `cd <another worktree> && git push ...`, and every git read below answered about the
# main checkout -- tip `0f309fd6` -- while the commit going out was `6271ceb5` in that other
# worktree, whose diff against `origin/main` touches no crate at all. The push was refused on a
# measurement of a repository it was not about. The same mix-up runs the other way and is worse:
# a push of unproven game code from one working tree passes whenever the directory this process
# happens to sit in has evidence of its own, which is the exact push this guard exists to stop.
# Cupcake pipes the whole pending event to every signal on stdin, so the command is readable here
# and the git reads can move to the checkout it names.
#
# What decides the answer is the sha in the log, never a timestamp. The first version compared
# mtimes and answered `OK` on a log written by a build two commits old that happened to still be
# running -- newer file, older code -- which is the exact failure being guarded, made inside the
# guard.
#
# The verdict on a push is the verdict of `scripts/check-runtime-evidence.sh`, the script the
# pre-push hook runs, called here rather than copied. Until 2026-10-19 this file kept its own
# carry-forward in a heredoc over a capped scan -- the newest two build shas only, on the theory
# that an older sha reaches the tip across strictly more commits. That holds on one branch and
# fails across several: the newest logs were another branch's tip (`90f5bcf6`), the run proving
# this branch's ancestor (`f8716805`, two docs/scripts-only commits under the tip `581fb93f`) sat
# in an older run directory past the cap, and this answered `MISSING` for a push the pre-push hook
# then accepted. One script deciding for both is how they stop disagreeing.
#
# Cost: a Bash call that is not a push answers before any evidence is read, so the warm path is
# still three git reads. A push pays the pre-push script's exhaustive scan, ~0.1s when a log names
# the tip and a few seconds when every older run has to be tried for carry-forward; the rulebook
# registers this signal with a 30 s timeout for that case, because a discovered signal is dropped
# at 5 s and a dropped signal is an allow.
set -uo pipefail

# The regression tests drive the policy through this, the same way the branch guards use
# CUPCAKE_CURRENT_BRANCH_OVERRIDE.
if [ -n "${CUPCAKE_RUNTIME_EVIDENCE_OVERRIDE:-}" ]; then
  printf '%s' "$CUPCAKE_RUNTIME_EVIDENCE_OVERRIDE"
  exit 0
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 0

command -v git >/dev/null 2>&1 || exit 0

# The pending event, when there is one. Guarded on a pipe: run by hand from a terminal this script
# has a keyboard on stdin, and reading that would hang instead of answering.
event=""
if [ ! -t 0 ]; then
  event="$(cat)"
fi

# Which working tree the push in that command would run in. The resolver is skipped unless the
# event mentions a push at all, which is nearly every Bash call, and that keeps the warm path free
# of a second python start. The test is sound in the direction that matters: the policy's own
# pattern requires the literal word, so an event without it cannot be a push there either.
#
# Every `grep -q` in this file reads a here-string, never a pipe. `-q` exits at its first match, so
# a `printf` still writing a long input hits a closed pipe, `pipefail` makes that write error the
# pipeline's status, and the test reads the opposite of what grep found. Measured 2026-10-13 against
# 8000 changed paths under crates/: the piped test below answered `NOTRUNTIME`.
target_dir=""
if grep -q 'push' <<<"$event"; then
  push_repo="$(printf '%s' "$event" |
    python3 "$repo_root/scripts/cupcake_push_target_repo.py" 2>/dev/null)"
  case "$push_repo" in
  UNKNOWN)
    # A redirect is present and could not be resolved to a working tree of this repository.
    # Falling back to this process's own directory is how the wrong repository came to be judged
    # in the first place, and `UNKNOWN` is the answer this signal already has for a question it
    # cannot see: it never denies, and the pre-push hook still measures the push exactly.
    printf 'UNKNOWN'
    exit 0
    ;;
  "REPO "*)
    target_dir="${push_repo#REPO }"
    ;;
  esac
fi

# Every git read that decides the verdict, aimed at the checkout being pushed. With no redirect in
# the command this is `git` exactly as before.
git_at() {
  if [ -n "$target_dir" ]; then
    git -C "$target_dir" "$@"
  else
    git "$@"
  fi
}

git_at rev-parse --git-dir >/dev/null 2>&1 || exit 0

head_sha="$(git_at rev-parse --short HEAD 2>/dev/null)" || exit 0
head_epoch="$(git_at log -1 --format=%ct HEAD 2>/dev/null)" || exit 0
[ -n "$head_epoch" ] || exit 0

# Which commits are about to go out. `origin/main` is the merge base for every branch in this repo;
# when it is unknown, fall back to the tip alone rather than guessing a range.
if git_at rev-parse --verify --quiet refs/remotes/origin/main >/dev/null 2>&1; then
  changed="$(git_at diff --name-only refs/remotes/origin/main...HEAD 2>/dev/null)"
else
  changed="$(git_at show --name-only --format= HEAD 2>/dev/null)"
fi

# Only code that ends up inside the game can be proven by a run. A push that moves docs, scripts or
# policies has nothing to demonstrate and must not be blocked -- a guard that fires on everything is
# one the next agent learns to override by reflex.
if ! grep -q '^crates/' <<<"$changed"; then
  printf 'NOTRUNTIME'
  exit 0
fi

# Nothing is being pushed, so there is nothing to judge, and the exhaustive scan below is not paid
# on every Bash call. The policy reads this verdict only for a push. A run with no event at all is
# someone at a terminal asking, and gets the full answer.
if [ -n "$event" ] && ! grep -q 'push' <<<"$event"; then
  printf 'UNKNOWN'
  exit 0
fi

# The verdict is `scripts/check-runtime-evidence.sh`'s, run the way `scripts/hooks/pre-push` runs
# it: from the top of the working tree being pushed, that tree's own copy of the script, with the
# variables that pin a repository unset. See the header for the disagreement this replaced.
toplevel="$(git_at rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$toplevel" ] || exit 0
checker="$toplevel/scripts/check-runtime-evidence.sh"
[ -f "$checker" ] || checker="$repo_root/scripts/check-runtime-evidence.sh"
[ -f "$checker" ] || exit 0

# 25 s, inside the 30 s the rulebook gives this signal, so a slow scan comes back as `UNKNOWN`
# from here rather than as a dropped signal.
report="$(
  cd "$toplevel" || exit 2
  # shellcheck disable=SC2046 # word splitting is the point: one variable name per word
  unset $(git rev-parse --local-env-vars 2>/dev/null)
  timeout 25 bash "$checker" "$head_sha" 2>&1 >/dev/null
)"
status=$?

case "$status" in
0)
  # Every allow the pre-push script has, told apart by the line it prints for each. A crate change
  # it does not count -- a deletion, under its `--diff-filter=d` -- prints nothing at all.
  if [ -z "$report" ]; then
    printf 'NOTRUNTIME'
  elif grep -q 'unmeasurable' <<<"$report"; then
    printf 'UNKNOWN'
  else
    printf 'OK'
  fi
  ;;
1)
  printf 'MISSING'
  ;;
*)
  # A timeout, or the script itself failed. That is not a verdict.
  printf 'UNKNOWN'
  ;;
esac
exit 0
