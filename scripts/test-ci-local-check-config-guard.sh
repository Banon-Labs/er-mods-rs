#!/usr/bin/env bash
# DOES THE ci-local-check GUARD ACTUALLY CATCH A GATE THAT REWRITES THE REPOSITORY CONFIG?
#
# scripts/ci-local-check.sh opens with a trap that compares core.bare and core.hooksPath in the
# SHARED git dir before and after the gate, because on 2026-08-31 the gate itself flipped
# core.bare to true and unset core.hooksPath in the live checkout -- twice, silently, from a push
# made in a linked worktree (bd hooks-selftest-under-git-hook-blanks-the-live-config-2026-08-31).
#
# A guard nobody has watched fail is a guard nobody knows is wired up. This proves it in both
# directions against the REAL text of that guard: the fixture is built by copying ci-local-check.sh
# up to and including its `trap` line, so if someone edits, moves or deletes the guard, this test
# changes with it or stops finding it -- it cannot drift into testing a stale copy of the logic.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source_script="$repo_root/scripts/ci-local-check.sh"

# The fixtures below run git commands in throwaway repositories. If THIS script inherits a git
# environment -- which is exactly the bug under test -- those commands would retarget the real
# checkout and the test would corrupt the thing it is defending.
# shellcheck disable=SC2046  # word splitting is the point: one variable name per word.
unset $(git rev-parse --local-env-vars)

tmp=$(mktemp -d "${TMPDIR:-/tmp}/er-ci-guard-test.XXXXXX")
trap 'rm -rf -- "$tmp"' EXIT

guard_end=$(grep -n '^trap gate_config_unchanged EXIT$' "$source_script" | head -1 | cut -d: -f1)
if [[ -z "$guard_end" ]]; then
	echo "[test-ci-local-check-config-guard] FAIL: no 'trap gate_config_unchanged EXIT' line in" >&2
	echo "  $source_script -- the guard this test exists to prove is gone, or was renamed." >&2
	exit 1
fi

# Build a fixture repo plus a REDUCED copy of ci-local-check.sh: its guard preamble, verbatim, and
# then one line standing in for "the gates". The real gates are a cargo cross-compile; the guard is
# indifferent to what runs between the trap and the exit, which is the whole point of it being a
# trap.
build_fixture() {
	local dir=$1 body=$2
	rm -rf -- "$dir"
	mkdir -p "$dir/scripts"
	git init -q "$dir"
	git -C "$dir" config core.hooksPath scripts/hooks
	head -n "$guard_end" "$source_script" >"$dir/scripts/ci-local-check.sh"
	printf '%s\n' "$body" >>"$dir/scripts/ci-local-check.sh"
}

fail=0

# --- negative control: a gate that touches nothing must stay green ------------------------------
build_fixture "$tmp/clean" 'echo "gates ran"'
if (cd "$tmp/clean" && bash scripts/ci-local-check.sh >/dev/null 2>&1); then
	echo "[test-ci-local-check-config-guard] ok -- an honest gate passes"
else
	echo "[test-ci-local-check-config-guard] FAIL: the guard rejected a gate that changed nothing." >&2
	echo "  A guard that fires on a clean run gets disabled by the next person to hit it." >&2
	fail=1
fi

# --- the 2026-08-31 damage, one key at a time --------------------------------------------------
# Two separate cases rather than one combined mutation: a guard that only noticed core.bare would
# still miss the ninety-minute ungated-push window, which was the hooksPath half.
check_case() {
	local name=$1 body=$2 expect=$3
	build_fixture "$tmp/$name" "$body"
	local out rc
	out=$(cd "$tmp/$name" && bash scripts/ci-local-check.sh 2>&1) && rc=0 || rc=$?
	if ((rc == 0)); then
		echo "[test-ci-local-check-config-guard] FAIL: $name was ACCEPTED. The gate rewrote the" >&2
		echo "  repository config and ci-local-check.sh exited 0 -- the silent failure, restored." >&2
		fail=1
		return
	fi
	if ! printf '%s' "$out" | grep -q "$expect"; then
		echo "[test-ci-local-check-config-guard] FAIL: $name was refused, but the message never" >&2
		echo "  named the key that changed (expected to see: $expect). Got:" >&2
		printf '%s\n' "$out" | sed 's/^/    /' >&2
		fail=1
		return
	fi
	echo "[test-ci-local-check-config-guard] ok -- $name is refused, and the message names the key"
}

check_case bare_flipped 'git config core.bare true' 'core.bare      false -> true'
check_case hookspath_unset 'git config --unset core.hooksPath' 'core.hooksPath scripts/hooks -> <unset>'

if ((fail)); then
	exit 1
fi
echo "[test-ci-local-check-config-guard] passed"
