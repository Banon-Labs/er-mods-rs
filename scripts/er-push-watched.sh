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
set -uo pipefail

local_ref=${1:?usage: er-push-watched.sh <local-ref> <remote-branch> [remote]}
remote_branch=${2:?usage: er-push-watched.sh <local-ref> <remote-branch> [remote]}
remote=${3:-origin}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
throttle="$script_dir/monitor-throttle.py"

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root" || exit 1

# Re-run once with every line throttled. The guard clause keeps the second pass from recursing, and
# an absent throttle is a hard stop rather than a silent unthrottled stream -- an unthrottled
# Monitor is refused anyway, so failing here says why instead of leaving the caller to guess.
if [ -z "${ER_PUSH_WATCHED_THROTTLED:-}" ]; then
	if [ ! -f "$throttle" ]; then
		printf 'push: refusing -- %s is missing, so this stream cannot be throttled\n' "$throttle" >&2
		exit 2
	fi
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
