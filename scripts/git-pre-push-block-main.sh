#!/usr/bin/env bash
set -euo pipefail

remote_name=${1:-unknown}
remote_url=${2:-unknown}
blocked=0

block_push() {
	local reason=$1
	local local_ref=${2:-unknown}
	local remote_ref=${3:-unknown}
	cat >&2 <<EOF
ER-EFFECTS-BLOCK-MAIN-PUSH: refusing direct push involving main.
reason: ${reason}
remote: ${remote_name} (${remote_url})
local_ref: ${local_ref}
remote_ref: ${remote_ref}

Push a feature/tooling branch instead and update main through the review/merge path.
EOF
	blocked=1
}

current_branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)

# `|| [[ -n ... ]]` is load-bearing, not defensive padding. `read` returns non-zero when it hits
# EOF without a delimiter, and bash then skips the loop body -- so a final line with no trailing
# newline is silently dropped. scripts/hooks/pre-push fed exactly that shape: it captures git's
# stdin with `pushed=$(cat)`, which strips the trailing newline, and replayed it with
# `printf '%s'`. For a single-ref push -- the normal case -- that one line was the only line, so
# this loop saw nothing and `git push origin HEAD:refs/heads/main` from a feature branch walked
# straight through. Measured 2026-08-31. The wrapper now sends `printf '%s\n'`, but the guard
# must not depend on being fed politely: it is the last thing between an agent and main.
rows_seen=0
while read -r local_ref _local_sha remote_ref _remote_sha || [[ -n "${local_ref:-}" ]]; do
	rows_seen=$((rows_seen + 1))
	case "$local_ref" in
		refs/heads/main|main)
			block_push "local ref is main" "$local_ref" "$remote_ref"
			;;
	esac
	case "$remote_ref" in
		refs/heads/main|main)
			block_push "remote ref is main" "$local_ref" "$remote_ref"
			;;
	esac
done

# The ref list git sends on stdin is the authoritative answer to "what is being pushed", so the
# checkout's own branch only matters when that list is missing.
#
# This used to be an unconditional refusal at the top of the file: if the checkout was on main, the
# push was blocked whatever the ref list said. That is wrong in the one direction a guard must never
# be wrong in -- it refused safe work. `git push -u origin fix/some-branch` from a main checkout
# cannot update remote main, and git says so on stdin (`refs/heads/fix/some-branch` on both sides),
# yet the guard answered `reason: current checkout is local main` and named `refs/heads/main` as the
# local ref, which git had never mentioned. Measured 2026-09-13 while pushing a fix branch from the
# primary checkout; the agent's next move was to reach for a workaround, which is what an
# over-broad guard teaches.
#
# The fail-closed half is kept exactly as it was. An empty ref list from a main checkout is still
# refused, because `push.default` is `current` here: a bare `git push` from main sends nothing on
# stdin that this guard can read and would update remote main. No ref list means no evidence, and
# no evidence from main is a refusal.
if [[ "$rows_seen" -eq 0 && "$current_branch" == "main" ]]; then
	block_push "current checkout is local main and git sent no ref list" "refs/heads/main" "unknown"
fi

if [[ "$blocked" != 0 ]]; then
	exit 1
fi
