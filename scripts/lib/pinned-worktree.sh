# shellcheck shell=bash
# A detached git worktree pinned to one commit, reused between runs.
#
# Two gates answer a question about a commit rather than about a checkout, and both need a tree
# that holds exactly that commit and nothing else:
#
#   scripts/check-committed-compiles.sh -- does the commit compile (its own worktree,
#     .worktrees/committed-compiles, with the cargo target dir kept outside it).
#   scripts/hooks/pre-push -- does the commit pass scripts/check.sh, when the ref being pushed is
#     not the pushing checkout's `HEAD` (.worktrees/pre-push-gate, target/ kept inside it).
#
# The worktree is reused rather than made fresh because cargo fingerprints include the absolute
# source path: a new directory per run is a cold build every time (~10 minutes here), a stable one
# is incremental. Callers serialise on their own lock; nothing here locks.
#
# Every git command names its repository with -C. A hook runs with GIT_DIR exported in a linked
# worktree, and GIT_DIR outranks -C, so callers must have scrubbed `git rev-parse
# --local-env-vars` before calling in -- both current callers do, first thing.

# pinned_worktree_pin <repo> <worktree> <sha> [<clean-exclude-pattern>...]
#
# Leaves <worktree> checked out, detached, at <sha>, with every untracked and ignored file
# removed except those matching the given `git clean -e` patterns. The removal is the point: a
# file the previous commit under test left behind would otherwise make a commit that deleted it
# look whole.
pinned_worktree_pin() {
	local repo=$1 worktree=$2 sha=$3
	shift 3
	local keep=() pattern
	for pattern in "$@"; do keep+=(-e "$pattern"); done
	if [[ -d "$worktree/.git" || -f "$worktree/.git" ]]; then
		# `--force` twice: the first lets the detached checkout move even when the previous run
		# left the tree dirty, the second lets it discard an untracked file that a tracked file
		# in the target commit wants to occupy.
		git -C "$worktree" checkout --detach --force --force "$sha" >/dev/null 2>&1 ||
			{
				git -C "$repo" worktree remove --force "$worktree" >/dev/null 2>&1 || rm -rf -- "$worktree"
				git -C "$repo" worktree prune >/dev/null 2>&1 || true
				git -C "$repo" worktree add --detach --force "$worktree" "$sha" >/dev/null
			}
	else
		rm -rf -- "$worktree"
		git -C "$repo" worktree prune >/dev/null 2>&1 || true
		git -C "$repo" worktree add --detach --force "$worktree" "$sha" >/dev/null
	fi
	# -x because the interesting leftovers (a stray crate directory, a generated module) are
	# exactly the gitignored ones; -e patterns are still honoured under -x, which is how a caller
	# keeps a build cache that lives inside the tree.
	git -C "$worktree" clean -qxfd "${keep[@]}"
}

# pinned_worktree_link_sibling <real sibling dir> <worktree>
#
# The workspace uses `../fromsoftware-rs` path dependencies, resolved relative to the manifest, so
# a worktree at <dir>/<name> needs <dir>/fromsoftware-rs. Returns 1 when the real checkout is
# absent and leaves the reporting to the caller.
#
# Only ever replaces a symlink, never a real directory.
#
# `pwd -P`, not `pwd`. bash's logical pwd echoes back the path you arrived by, symlinks and all --
# so when the real path and the link name the same path, `ln -sfn` points the link at itself and
# every later read of it dies with ELOOP ("Too many levels of symbolic links"), which cargo reports
# as `failed to load manifest for dependency eldenring`. That happens whenever the invoking
# checkout is itself a worktree under `<repo>/.worktrees/` and the pinned worktree is in the same
# family's scratch dir: both then resolve to `<repo>/.worktrees/fromsoftware-rs`. Measured
# 2026-09-03; it also poisons the link for every later run, because the damage is on disk.
pinned_worktree_link_sibling() {
	local real=$1 worktree=$2 link
	link="$(dirname -- "$worktree")/fromsoftware-rs"
	[[ -d "$real" ]] || return 1
	if [[ -L "$link" || ! -e "$link" ]]; then
		ln -sfn "$(cd -- "$real" && pwd -P)" "$link"
	fi
}
