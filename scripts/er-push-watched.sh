#!/usr/bin/env bash
# Run one push in the foreground and stream its gate verdicts, so the thing that started the push
# is also the thing that learns it finished.
#
# The failure this exists to stop, measured 2026-09-14. A push here runs the whole local gate suite
# and takes ten to fifteen minutes, which is past the harness cap on a backgrounded command, so the
# agent reached for `setsid nohup git push ... &`. That detaches the push from the harness entirely:
# nothing is left to report an exit, and the only way back to the result is for the agent to go and
# read the log by hand. It did not. The push failed at 09:29:45 and was noticed at 09:59:07 -- 29
# minutes of a user waiting on a run that was already over, and a monitor armed afterwards against
# a file nothing was writing to any more, which replayed the history once and then watched a dead
# log until it was killed.
#
# So the shape is: the push runs here, in this process, with its output on stdout. Point a Monitor
# at this script and the monitor process is itself the push -- every stage verdict arrives as it
# happens, and the monitor ends when the push ends, which is the notification. There is nothing to
# poll and nothing to tear down separately.
#
# Usage, and the whole command a Monitor needs -- the throttle is applied inside, so the caller
# names one absolute path and no second one:
#
#   bash /abs/path/scripts/er-push-watched.sh <local-ref> <remote-branch> [remote]
#
# Throttling is not optional: cupcake refuses an unthrottled Monitor, because a live log has no
# natural rate and one backed-off line once notified for minutes. It is applied here rather than
# left to the caller for a reason measured the same day. A Monitor was armed as
# `... | python3 /main/tree/scripts/monitor-throttle.py 15`, reaching out of a worktree into the
# main checkout for that helper; the main checkout then switched branch, the helper was on a branch
# the new one did not carry, python3 exited 2, and the SIGPIPE killed the push six minutes in. Both
# helpers are resolved relative to this file instead, so the push depends on the tree it runs in
# and on nothing else.
#
# A third incident, the same day, and the reason this script now refuses some pushes outright. An
# orchestrator pushed `worktree-agent-<id>` to PR #448's branch, and that local branch had never
# moved: the agent's brief had deliberately broken the session's pattern and sent it to commit on
# the PR branch instead, so its worktree branch still pointed at the main it was created from. The
# push therefore sent main's commit to the PR branch and took two commits off it, and getting them
# back needed a `--force-with-lease` pinned to the exact sha that had just been overwritten. Three
# correct pushes earlier in the same session are what made it easy: the fourth was typed from the
# pattern rather than from the brief that had changed it.
#
# The tell was one command away and free -- `git log --oneline -2 <ref>` would have shown main's
# commit sitting on top -- so it is run here rather than remembered. Before the push, and before
# the ten-to-fifteen-minute gate suite the push triggers, this refuses any local ref that carries
# no commit main does not already have. See `refuse_a_ref_that_carries_no_work` below for the
# predicate, for why main is fetched rather than read out of refs/remotes, and for the one
# deliberately spelled way past it.
set -uo pipefail

local_ref=${1:?usage: er-push-watched.sh <local-ref> <remote-branch> [remote]}
remote_branch=${2:?usage: er-push-watched.sh <local-ref> <remote-branch> [remote]}
remote=${3:-origin}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
throttle="$script_dir/monitor-throttle.py"

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root" || exit 1

# Exit codes of the refusal below: 3 says the ref names no work, 4 says the question could not be
# answered. Two rather than one because they ask the reader for different things -- 3 means go and
# find your commits on another branch, 4 means fix the repository or the network and run it again.
readonly EXIT_REF_CARRIES_NO_WORK=3
readonly EXIT_CANNOT_ANSWER=4

# Does the ref being pushed carry any work at all?
#
# The predicate is `git rev-list --count <main>..<ref>` equal to zero. That is the same set of refs
# as "this ref is an ancestor of main", and it is spelled as a count because the count is the
# question -- how many commits would this push add that main does not already have -- and because
# `scripts/check-no-local-main-commits.sh` already asks its mirror-image question the same way.
#
# The narrower test, ref tip equal to main tip, would have caught the 2026-09-14 push exactly and
# nothing else. A worktree branch points at the main it was created from, so once main moves on,
# the identical mistake made an hour later leaves the ref an ancestor rather than the tip, and the
# narrow test waves it through. The wider one costs nothing: the only extra case it catches is a
# branch with no commits of its own, which is not a push anyone means to make.
#
# It cannot catch a deletion, and does not need to. A deletion is spelled with an empty source ref,
# and there is no way to spell one here: the first argument is `${1:?}`, which refuses an absent or
# empty value, and the refspec is built as `$local_ref:refs/heads/$remote_branch`, so the left of
# the colon is always a ref this script was handed. `git push origin --delete <branch>` does not
# come through this file at all.
#
# Main is read by fetching it, not out of refs/remotes. A stale tracking ref fails in the dangerous
# direction: the branch in the incident pointed at the main of the moment its worktree was created,
# so a local view of main older than that would have counted commits on it and let the push
# through. The push is a network operation anyway, so making the comparison one costs it nothing it
# was not already paying. `FETCH_HEAD` is read rather than `refs/remotes/<remote>/main` because the
# third argument may be a filesystem path rather than a remote name and a path has no tracking ref;
# it is a per-worktree pseudoref that every `git pull` rewrites, so nothing durable is clobbered.
# Anything that cannot be resolved -- an unknown ref, an unreachable remote, a remote with no main
# -- refuses rather than guesses.
refuse_a_ref_that_carries_no_work() {
	local tip base ahead

	# The one way past, spelled out rather than inferred, for the push that really does mean to
	# point a remote branch at main: abandoning a pull request's branch, or opening one from a
	# branch that has no commits of its own yet.
	if [ -n "${ER_PUSH_WATCHED_ALLOW_NO_COMMITS:-}" ]; then
		printf 'push: ER_PUSH_WATCHED_ALLOW_NO_COMMITS is set -- not asking whether %s carries any commit\n' \
			"$local_ref"
		return 0
	fi

	if ! tip=$(git rev-parse --verify --quiet "$local_ref^{commit}"); then
		printf 'push: refusing -- %s does not name a commit in %s, so there is nothing to compare against main\n' \
			"$local_ref" "$repo_root" >&2
		exit "$EXIT_CANNOT_ANSWER"
	fi

	if ! timeout 25 git fetch --quiet "$remote" main; then
		printf 'push: refusing -- could not fetch main from %s, and the alternative is comparing %s against a local view of main that may be old\n' \
			"$remote" "$local_ref" >&2
		exit "$EXIT_CANNOT_ANSWER"
	fi

	if ! base=$(git rev-parse --verify --quiet 'FETCH_HEAD^{commit}'); then
		printf 'push: refusing -- the fetch from %s left no FETCH_HEAD to read main out of\n' \
			"$remote" >&2
		exit "$EXIT_CANNOT_ANSWER"
	fi

	if ! ahead=$(git rev-list --count "$base..$tip"); then
		printf 'push: refusing -- could not count the commits on %s that main does not have\n' \
			"$local_ref" >&2
		exit "$EXIT_CANNOT_ANSWER"
	fi

	if [ "$ahead" -gt 0 ]; then
		return 0
	fi

	# Both tips and both subjects, because the point is to let the reader see the mistake rather
	# than only be stopped by it: main's own subject line sitting on the branch is the whole tell.
	{
		printf 'push: REFUSED -- %s carries no commit that main does not already have.\n' "$local_ref"
		printf '\n'
		printf '  %-30s %s  %s\n' "$local_ref" \
			"$(git rev-parse --short "$tip")" "$(git log -1 --format=%s "$tip")"
		printf '  %-30s %s  %s\n' "$remote/main (just fetched)" \
			"$(git rev-parse --short "$base")" "$(git log -1 --format=%s "$base")"
		printf '  %-30s %s\n' 'commits on it main lacks' "$ahead"
		printf '\n'
		printf '  That is the shape of a branch that never moved. A worktree subagent is created on\n'
		printf '  worktree-agent-<id>, and when its brief sends it to commit on some other branch instead,\n'
		printf '  that branch still points at the main it was created from. Pushing it would overwrite\n'
		printf '  %s/%s with main; on 2026-09-14 that took two commits off PR #448.\n' \
			"$remote" "$remote_branch"
		printf '\n'
		printf '  Find where the work actually went before pushing anything:\n'
		printf '      git log --oneline -3 %s\n' "$local_ref"
		printf '      git for-each-ref --contains <the-sha-you-expect> --format="%%(refname:short)"\n'
		printf '\n'
		printf '  If pointing %s/%s at main really is the intent, say so:\n' "$remote" "$remote_branch"
		printf '      ER_PUSH_WATCHED_ALLOW_NO_COMMITS=1 bash %s %s %s %s\n' \
			"${BASH_SOURCE[0]}" "$local_ref" "$remote_branch" "$remote"
	} >&2
	exit "$EXIT_REF_CARRIES_NO_WORK"
}

# Re-run once with every line throttled. The guard clause keeps the second pass from recursing, and
# an absent throttle is a hard stop rather than a silent unthrottled stream -- an unthrottled
# Monitor is refused anyway, so failing here says why instead of leaving the caller to guess.
if [ -z "${ER_PUSH_WATCHED_THROTTLED:-}" ]; then
	if [ ! -f "$throttle" ]; then
		printf 'push: refusing -- %s is missing, so this stream cannot be throttled\n' "$throttle" >&2
		exit 2
	fi
	# Here, above the re-exec, for two reasons. It runs once instead of twice, so one fetch answers
	# the question. And its output is not throttled: the throttle emits at most one line per fifteen
	# seconds and shows only the last line it was holding, so a multi-line refusal sent through it
	# would reach the reader as a single stray line with a suppression count beside it.
	refuse_a_ref_that_carries_no_work
	ER_PUSH_WATCHED_THROTTLED=1 bash "${BASH_SOURCE[0]}" "$local_ref" "$remote_branch" "$remote" |
		python3 "$throttle" 15
	exit "${PIPESTATUS[0]}"
fi

# The refspec is spelled out rather than left to push.default, so the guard that reads the ref list
# on stdin sees a destination that cannot be main, and so a push from a checkout sitting on another
# branch still sends the branch that was asked for.
printf 'push: %s -> %s/%s\n' "$local_ref" "$remote" "$remote_branch"

# Only the lines worth a notification. The failure signatures sit in the same alternation as the
# progress ones deliberately: a filter matching only stage verdicts goes silent on a refusal, and
# silence is indistinguishable from a run still going.
git push --force-with-lease "$remote" \
	"$local_ref:refs/heads/$remote_branch" 2>&1 |
	grep -E --line-buffered \
		-e '^>>> stage' \
		-e 'RED \(exit' \
		-e '^  FAILED ' \
		-e '^RED --' \
		-e 'REFUSED' \
		-e 'error: failed to push' \
		-e 'rejected' \
		-e "-> $remote_branch"

status=${PIPESTATUS[0]}
if [ "$status" -eq 0 ]; then
	printf 'push: ok %s -> %s/%s\n' "$local_ref" "$remote" "$remote_branch"
else
	printf 'push: failed (exit %s) %s -> %s/%s\n' "$status" "$local_ref" "$remote" "$remote_branch"
fi
exit "$status"
