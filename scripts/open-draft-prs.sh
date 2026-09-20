#!/usr/bin/env bash
# Push a set of local branches and open a draft pull request for each, in one invocation.
#
#     scripts/open-draft-prs.sh --base <ref> [options] [<branch>...]
#     scripts/open-draft-prs.sh --selftest
#
# The agent cannot run this: `.cupcake/policies/claude/git_block_any_push.rego` refuses every agent
# push, and a pull request needs the branch on the remote first. It exists to be handed over, which
# is why it is worth generalising rather than writing a fresh one-off each time.
#
# Two things it knows that a hand-typed loop does not:
#
# 1. Where to push from. `scripts/hooks/pre-push` skips `scripts/check.sh` when the pushing
#    checkout matches `*/.claude/worktrees/agent-*`, and runs the whole suite otherwise -- ten
#    minutes with every core pinned. Branch refs live in the shared object store, so any checkout
#    can push any branch; this picks an agent worktree when one exists and says so when it has to
#    fall back to a checkout that will pay the full suite.
# 2. How many pushes to make: one, carrying every ref. Git hands the hook a single stdin listing
#    them all, so the compile gate runs once for five branches instead of five times. A ref can be
#    pushed from any checkout whether or not another worktree has it checked out, so there is never
#    a reason to split the push per branch.
#
# Title comes from the commit subject. Body comes from `<body-dir>/<branch with / as ->.md` when
# that file exists, and from the commit body otherwise -- so a long review narrative lives in a
# file beside the run rather than inside this script.

set -euo pipefail

BASE=""
BODY_DIR=""
DRY_RUN=0
DRAFT_FLAG="--draft"
declare -a BRANCHES=()

usage() {
	cat <<'USAGE'
usage: scripts/open-draft-prs.sh --base <ref> [options] [<branch>...]

  --base <ref>       base branch for every pull request (required unless --selftest)
  --body-dir <dir>   look for <dir>/<branch with / as ->.md as the body; falls back to the
                     commit body when the file is absent
  --no-draft         open ready-for-review instead of draft (the repo default is draft)
  --dry-run          print the push and the pull-request calls without running them
  --selftest         exercise the parsing and the worktree choice, touching no remote
  -h, --help         this text

With no <branch> arguments, every local branch whose merge-base with --base is an ancestor of
--base, and which origin does not already carry, is taken.
USAGE
}

# The branch's body file, spelled so a slash in a branch name cannot escape the directory.
body_file_for() {
	local branch="$1"
	printf '%s/%s.md' "$BODY_DIR" "${branch//\//-}"
}

# The one checkout every branch is pushed from, and whether it pays for the full suite.
#
# Prints "<path>\t<skips_check>". Deliberately one answer for the whole run rather than one per
# branch: a push names refs, and a ref can be pushed from any checkout in the repository whether or
# not some other worktree has it checked out. Choosing each branch's own holder looked tidier and
# was measured doing the opposite of what this script is for -- five branches each held in their
# own agent worktree became five pushes, so the pre-push compile gate ran five times.
#
# An agent worktree is preferred because `scripts/hooks/pre-push` skips `scripts/check.sh` there.
push_checkout() {
	local line path agentish=""
	while IFS= read -r line; do
		case "$line" in
		worktree\ *)
			path="${line#worktree }"
			if [[ -z "$agentish" && "$path" == */.claude/worktrees/agent-* ]]; then agentish="$path"; fi
			;;
		esac
	done < <(git worktree list --porcelain)

	local chosen="${agentish:-$(git rev-parse --show-toplevel)}"
	local skips=no
	[[ "$chosen" == */.claude/worktrees/agent-* ]] && skips=yes
	printf '%s\t%s\n' "$chosen" "$skips"
}

# Local branches that are candidates when the caller named none.
discover_branches() {
	local branch
	while IFS= read -r branch; do
		[[ "$branch" == "$BASE" ]] && continue
		git merge-base --is-ancestor "$(git merge-base "$branch" "$BASE")" "$BASE" 2>/dev/null || continue
		git show-ref --quiet "refs/remotes/origin/$branch" && continue
		[[ "$(git rev-parse "$branch")" == "$(git rev-parse "$BASE")" ]] && continue
		printf '%s\n' "$branch"
	done < <(git for-each-ref --format='%(refname:short)' refs/heads/)
}

selftest() {
	local failures=0 checks=0
	check() {
		checks=$((checks + 1))
		if [[ "$2" == "$3" ]]; then
			printf '  ok    %s\n' "$1"
		else
			printf '  FAIL  %s\n        want %q\n        got  %q\n' "$1" "$3" "$2"
			failures=$((failures + 1))
		fi
	}

	BODY_DIR=/tmp/bodies
	check "a slash in a branch name becomes a dash" \
		"$(body_file_for feat/thing)" "/tmp/bodies/feat-thing.md"
	check "two slashes both become dashes" \
		"$(body_file_for a/b/c)" "/tmp/bodies/a-b-c.md"
	check "a plain name is unchanged" \
		"$(body_file_for thing)" "/tmp/bodies/thing.md"
	BODY_DIR=""

	# The worktree choice, read off this repository's real worktree list rather than a fixture,
	# because the property under test is about this repository's actual shape.
	#
	# The agent count is taken with `awk` rather than `grep -q`, which exits on the first match and
	# leaves `git worktree list` holding a closed pipe. Under `set -o pipefail` that SIGPIPE becomes
	# the pipeline's status, so the probe answered "no agent worktree" in a checkout that had 108 of
	# them, and this branch of the selftest skipped silently instead of running.
	local chosen skips agent_count
	IFS=$'\t' read -r chosen skips < <(push_checkout)
	agent_count=$(git worktree list --porcelain |
		awk '/^worktree .*\/\.claude\/worktrees\/agent-/ { n++ } END { print n + 0 }')

	# Matched as a shape, not compared to a literal: which of the 108 agent worktrees is first is
	# not a property worth pinning, but that the answer is one of them is the whole point.
	local is_agent=no
	case "$chosen" in */.claude/worktrees/agent-*) is_agent=yes ;; esac

	if [[ "$agent_count" -gt 0 ]]; then
		check "an agent worktree is preferred when one exists" "$is_agent" "yes"
		check "and that choice is reported as skipping check.sh" "$skips" "yes"
	else
		check "with no agent worktree the push falls back to this checkout" \
			"$chosen" "$(git rev-parse --show-toplevel)"
		check "and says it will pay for the full suite" "$skips" "no"
	fi

	printf 'selftest: %s (%d checks, %d failures)\n' \
		"$([[ $failures -eq 0 ]] && echo PASS || echo FAIL)" "$checks" "$failures"
	return "$failures"
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--base) BASE="${2:?--base needs a ref}"; shift 2 ;;
	--body-dir) BODY_DIR="${2:?--body-dir needs a directory}"; shift 2 ;;
	--no-draft) DRAFT_FLAG=""; shift ;;
	--dry-run) DRY_RUN=1; shift ;;
	--selftest) selftest; exit $? ;;
	-h | --help) usage; exit 0 ;;
	-*) printf 'unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
	*) BRANCHES+=("$1"); shift ;;
	esac
done

[[ -n "$BASE" ]] || { printf -- '--base is required\n\n' >&2; usage >&2; exit 2; }
git rev-parse --verify --quiet "$BASE" >/dev/null || { printf 'no such base ref: %s\n' "$BASE" >&2; exit 2; }

if [[ ${#BRANCHES[@]} -eq 0 ]]; then
	mapfile -t BRANCHES < <(discover_branches)
	[[ ${#BRANCHES[@]} -gt 0 ]] || { printf 'nothing to open: no unpushed branch sits on %s\n' "$BASE"; exit 0; }
	printf 'discovered %d unpushed branch(es) on %s:\n' "${#BRANCHES[@]}" "$BASE"
	printf '  %s\n' "${BRANCHES[@]}"
	printf '\n'
fi

for branch in "${BRANCHES[@]}"; do
	git rev-parse --verify --quiet "refs/heads/$branch" >/dev/null ||
		{ printf 'no such local branch: %s\n' "$branch" >&2; exit 2; }
done

# One push carrying every ref, so git hands the hook a single stdin and the compile gate runs once.
IFS=$'\t' read -r checkout skips < <(push_checkout)

printf '== pushing %d branch(es) from %s\n' "${#BRANCHES[@]}" "$checkout"
if [[ "$skips" == no ]]; then
	printf '   this checkout runs the full scripts/check.sh in pre-push; expect ~10 minutes\n'
fi
if [[ $DRY_RUN -eq 1 ]]; then
	printf '   would run: git -C %q push -u origin %s\n' "$checkout" "${BRANCHES[*]}"
else
	git -C "$checkout" push -u origin "${BRANCHES[@]}"
fi

printf '\n== opening pull requests against %s\n' "$BASE"
for branch in "${BRANCHES[@]}"; do
	title=$(git log -1 --format='%s' "$branch")
	body=""
	if [[ -n "$BODY_DIR" && -f "$(body_file_for "$branch")" ]]; then
		body=$(cat "$(body_file_for "$branch")")
	else
		body=$(git log -1 --format='%b' "$branch")
	fi
	[[ -n "${body// /}" ]] || body="$title"

	if [[ $DRY_RUN -eq 1 ]]; then
		printf '   would open %s: %s (body %d bytes)\n' "$branch" "$title" "${#body}"
	else
		gh pr create ${DRAFT_FLAG:+$DRAFT_FLAG} --base "$BASE" --head "$branch" \
			--title "$title" --body "$body"
	fi
done

[[ $DRY_RUN -eq 1 ]] || gh pr list --base "$BASE" --limit 20
