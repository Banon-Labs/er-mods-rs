#!/usr/bin/env bash
# Prove `scripts/er-push-watched.sh` refuses to push a local ref that carries no commit main does
# not already have, before it runs the push or anything the push would trigger.
#
# The defect this pins, measured 2026-09-14. An orchestrator pushed `worktree-agent-<id>` to PR
# #448's branch and overwrote it with main. That local branch had never moved: three earlier pushes
# in the session had correctly used the `worktree-agent-<id>` pattern, and the fourth agent's brief
# had deliberately broken it by sending the agent to commit on the PR branch instead. So the ref
# that was pushed still pointed at the main its worktree was created from, the PR branch lost two
# commits, and recovery needed a `--force-with-lease` pinned to the sha that had just been
# overwritten. `git log --oneline -2 <ref>` would have shown main's own subject sitting on top.
#
# Why the fixture is built rather than the answers typed. Every assertion here turns on what git
# itself does -- what `--force-with-lease` accepts, what a fetch writes to `FETCH_HEAD`, what
# `rev-list --count` returns across a stale tracking ref -- so the cases drive real `git push` and
# `git fetch` invocations against a real bare remote under mktemp. A test that asserted those
# answers from this file would be proving its own author's guess.
#
# Why a stub pre-push hook. In the real repository that hook is where `scripts/check.sh` runs, and
# ten to fifteen minutes of gates ending in the wrong push is the waste being closed. The fixture
# installs a hook that only records that it ran, so "the refusal happened before any gate" is an
# observable fact rather than a claim about where a line sits in a file.
#
# Nothing here touches the repository it lives in: every git invocation names the fixture, and the
# remote is always a path under mktemp.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
push_watched="$repo_root/scripts/er-push-watched.sh"

fail=0
ok() { printf '  ok    %s\n' "$1"; }
bad() {
	printf '  FAIL  %s\n' "$1" >&2
	fail=1
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/er-quickload-push-watched.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

# `env -u ...` for the same reason scripts/test-pre-push-deletion-only.sh does it: this suite is
# itself run by the pre-push hook, and in a linked worktree git exports GIT_DIR to that hook. A
# `git init` that inherits it builds no fixture and edits this repository instead, and so does the
# script under test, which resolves its own repository with `git rev-parse --show-toplevel`.
git_clean=(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE git)

remote="$tmp/remote.git"
work="$tmp/work"
"${git_clean[@]}" init -q --bare "$remote"
"${git_clean[@]}" init -q -b main "$work"
"${git_clean[@]}" -C "$work" config user.email selftest@example.invalid
"${git_clean[@]}" -C "$work" config user.name selftest
"${git_clean[@]}" -C "$work" config commit.gpgsign false
"${git_clean[@]}" -C "$work" config core.hooksPath "$tmp/hooks"
"${git_clean[@]}" -C "$work" remote add origin "$remote"

mkdir -p "$tmp/hooks" "$tmp/no-hooks-here"
cat >"$tmp/hooks/pre-push" <<'HOOK'
#!/usr/bin/env bash
# Stands in for the gate suite. In the real repository this hook ends in scripts/check.sh.
printf 'ran\n' >>"$GATE_TRACE"
exit 0
HOOK
chmod +x "$tmp/hooks/pre-push"
export GATE_TRACE="$tmp/gate-trace"
: >"$GATE_TRACE"

# Building the fixture must not run the stub hook, or the trace is dirty before the first case.
# Pointing core.hooksPath at a directory with no hook in it is git's own way of running none.
setup_push() { # setup_push <refspec...>
	"${git_clean[@]}" -C "$work" -c core.hooksPath="$tmp/no-hooks-here" push -q origin "$@"
}

commit_on() { # commit_on <branch> <message> -- check it out and add one empty commit
	"${git_clean[@]}" -C "$work" checkout -q "$1" 2>/dev/null ||
		"${git_clean[@]}" -C "$work" checkout -q -b "$1"
	"${git_clean[@]}" -C "$work" commit -q --allow-empty -m "$2"
}

sha_of() { # sha_of <ref> -- in the working clone
	"${git_clean[@]}" -C "$work" rev-parse --verify --quiet "$1^{commit}"
}

remote_sha_of() { # remote_sha_of <branch> -- as the bare remote itself holds it
	"${git_clean[@]}" -C "$remote" rev-parse --verify --quiet "refs/heads/$1^{commit}"
}

# The script under test resolves its repository from the current directory, so every run is made
# from inside the fixture working tree with git's repository variables scrubbed.
run_push() { # run_push <local-ref> <remote-branch> [remote] -- captures output, returns its status
	local status
	push_output=$(
		cd "$work" &&
			env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
				bash "$push_watched" "$@" 2>&1
	)
	status=$?
	return "$status"
}

# main gets two commits, so there is an ancestor of main that is not its tip to test with. The
# first is made directly: `git checkout main` has nothing to check out while the branch is unborn.
"${git_clean[@]}" -C "$work" commit -q --allow-empty -m "the first commit"
main_first=$(sha_of main)
commit_on main "chore: a commit already on main"
main_tip=$(sha_of main)
setup_push main

# A branch with work of its own, and one whose commit is later swallowed by main.
commit_on feat/work "feat: the work that should reach the remote"
work_tip=$(sha_of feat/work)
"${git_clean[@]}" -C "$work" checkout -q main
commit_on feat/merged "feat: a commit main will take over"
merged_tip=$(sha_of feat/merged)
"${git_clean[@]}" -C "$work" checkout -q main

# The branch that never moved: created off main and never committed to, which is the incident.
"${git_clean[@]}" -C "$work" branch worktree-agent-fixture main
# ...and a stale one, sitting on an ancestor of main rather than on its tip.
"${git_clean[@]}" -C "$work" branch stale/behind "$main_first"

# Destinations, created on the remote and fetched, so `--force-with-lease` has a remote-tracking
# ref to take its lease from on the cases that do push.
setup_push "main:refs/heads/pr-clobbered" "main:refs/heads/pr-work" "main:refs/heads/pr-optout"
"${git_clean[@]}" -C "$work" -c core.hooksPath="$tmp/no-hooks-here" fetch -q origin

# A ref carrying no work is refused, and nothing downstream of the refusal happens.
: >"$GATE_TRACE"
before=$(remote_sha_of pr-clobbered)
if run_push worktree-agent-fixture pr-clobbered origin; then
	bad "a branch sitting on main's tip was pushed instead of refused"
else
	status=$?
	if [ "$status" -eq 3 ]; then
		ok "a branch sitting on main's tip is refused with exit 3"
	else
		bad "a branch sitting on main's tip refused with exit $status, wanted 3"
	fi
fi
if [ "$(remote_sha_of pr-clobbered)" = "$before" ]; then
	ok "the refused push left pr-clobbered where it was"
else
	bad "the refused push moved pr-clobbered anyway"
fi
if [ -s "$GATE_TRACE" ]; then
	bad "the refusal came after the pre-push gates ran, which is the waste this closes"
else
	ok "the refusal came before the pre-push gates ran"
fi

# The refusal has to be readable, not just fatal: the reader should see main's subject sitting on
# the branch and recognise the mistake.
refusal=$push_output
short_tip=$("${git_clean[@]}" -C "$work" rev-parse --short "$main_tip")
for needle in \
	"worktree-agent-fixture" \
	"$short_tip" \
	"chore: a commit already on main" \
	"origin/main (just fetched)" \
	"never moved" \
	"ER_PUSH_WATCHED_ALLOW_NO_COMMITS=1"; do
	if [[ "$refusal" == *"$needle"* ]]; then
		ok "the refusal names '$needle'"
	else
		bad "the refusal does not name '$needle'"
	fi
done

# A branch that really does carry work is pushed, and the gates run for it.
: >"$GATE_TRACE"
if run_push feat/work pr-work origin; then
	ok "a branch with a commit of its own is pushed"
else
	bad "a branch with a commit of its own was refused: $push_output"
fi
if [ "$(remote_sha_of pr-work)" = "$work_tip" ]; then
	ok "pr-work now holds the commit that was pushed"
else
	bad "pr-work did not move to the pushed commit"
fi
if [ -s "$GATE_TRACE" ]; then
	ok "the allowed push ran the pre-push gates"
else
	bad "the allowed push never reached the pre-push hook"
fi

# An ancestor of main that is not its tip is refused too. This is the half the narrow predicate
# (ref tip equal to main tip) would miss: the same mistake made after main has moved on.
: >"$GATE_TRACE"
if run_push stale/behind pr-clobbered origin; then
	bad "a branch on an ancestor of main was pushed instead of refused"
else
	status=$?
	if [ "$status" -eq 3 ]; then
		ok "a branch on an ancestor of main is refused with exit 3"
	else
		bad "a branch on an ancestor of main refused with exit $status, wanted 3"
	fi
fi

# The opt-out, for the push that really does mean to point a branch at main.
: >"$GATE_TRACE"
if (
	cd "$work" &&
		env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
			ER_PUSH_WATCHED_ALLOW_NO_COMMITS=1 \
			bash "$push_watched" worktree-agent-fixture pr-optout origin >/dev/null 2>&1
); then
	ok "ER_PUSH_WATCHED_ALLOW_NO_COMMITS lets a deliberate no-commit push through"
else
	bad "ER_PUSH_WATCHED_ALLOW_NO_COMMITS did not let a deliberate no-commit push through"
fi
if [ "$(remote_sha_of pr-optout)" = "$main_tip" ]; then
	ok "the opted-out push landed on the remote"
else
	bad "the opted-out push did not reach the remote"
fi

# Everything unanswerable refuses rather than guesses.
: >"$GATE_TRACE"
if run_push no/such/branch pr-clobbered origin; then
	bad "a ref that does not resolve was pushed"
else
	status=$?
	if [ "$status" -eq 4 ]; then
		ok "a ref that does not resolve is refused with exit 4"
	else
		bad "a ref that does not resolve refused with exit $status, wanted 4"
	fi
fi
if [ -s "$GATE_TRACE" ]; then
	bad "the unresolvable ref still reached the pre-push hook"
else
	ok "the unresolvable ref never reached the pre-push hook"
fi

if run_push feat/work pr-clobbered "$tmp/there-is-no-remote-here.git"; then
	bad "a push to an unreachable remote was attempted rather than refused"
else
	status=$?
	if [ "$status" -eq 4 ]; then
		ok "an unreachable remote is refused with exit 4"
	else
		bad "an unreachable remote refused with exit $status, wanted 4"
	fi
fi

"${git_clean[@]}" init -q --bare "$tmp/no-main.git"
if run_push feat/work pr-clobbered "$tmp/no-main.git"; then
	bad "a remote with no main was pushed to rather than refused"
else
	status=$?
	if [ "$status" -eq 4 ]; then
		ok "a remote with no main is refused with exit 4"
	else
		bad "a remote with no main refused with exit $status, wanted 4"
	fi
fi

# A deletion is not routed through this script and cannot be spelled to it. The first argument is
# `${1:?}`, so an empty source ref -- which is how a deletion is written -- never gets as far as
# the guard, and the refspec is always `<the ref it was handed>:refs/heads/<branch>`.
if run_push "" pr-clobbered origin; then
	bad "an empty local ref, which is how a deletion is spelled, was accepted"
else
	ok "an empty local ref is rejected by the usage check, so no deletion can be spelled here"
fi
# Both patterns are shell source being matched as text, so they must not expand here.
# shellcheck disable=SC2016
refspec_source='"$local_ref:refs/heads/$remote_branch"'
# shellcheck disable=SC2016
push_source='git push --force-with-lease "$remote"'
if grep -Fq "$push_source" "$push_watched" && grep -Fq "$refspec_source" "$push_watched"; then
	ok "the refspec always names the ref this script was handed, so its left side is never empty"
else
	bad "the refspec is no longer built from \$local_ref; re-check whether a deletion can reach it"
fi

# Main is fetched, not read out of refs/remotes, and this is the case that separates the two. The
# remote's main is moved forward to swallow feat/merged while the clone still believes main is
# where it was, so a comparison against the tracking ref counts a commit that is not really there.
: >"$GATE_TRACE"
# The remote has to hold the object before its main can point at it, and sending it under another
# name leaves the clone's view of origin/main untouched, which is the whole point of the case.
setup_push "feat/merged:refs/heads/feat-merged"
"${git_clean[@]}" -C "$remote" update-ref refs/heads/main "$merged_tip"
stale_count=$("${git_clean[@]}" -C "$work" rev-list --count "refs/remotes/origin/main..$merged_tip")
if [ "$stale_count" = "1" ]; then
	ok "the stale tracking ref would have reported 1 commit of work, which is the trap"
else
	bad "the staleness fixture is not set up: stale count is $stale_count, wanted 1"
fi
if run_push feat/merged pr-clobbered origin; then
	bad "a stale local view of main let a branch with no real work through"
else
	status=$?
	if [ "$status" -eq 3 ]; then
		ok "main is fetched, so a stale tracking ref does not let the push through"
	else
		bad "the stale-tracking-ref case refused with exit $status, wanted 3"
	fi
fi

if [ "$fail" -ne 0 ]; then
	printf 'test-er-push-watched-ref-carries-work: FAILED\n' >&2
	exit 1
fi
printf 'test-er-push-watched-ref-carries-work: all checks passed\n'
