#!/usr/bin/env bash
# DOES THE COMMITTED STATE COMPILE?  -- not "does my working tree compile".
#
# WHY THIS IS A DIFFERENT QUESTION, and why every other gate in this repo answers the wrong one.
# Agents here commit with explicit pathspecs (correctly -- several of them share this checkout and
# a bare `git commit -a` would sweep up each other's work). A pathspec is also the exact mechanism
# by which a CONSUMER gets committed without its PRODUCER: the new caller is named on the command
# line, the new function/crate it calls is not. The author's working tree still holds the producer,
# so it compiles for them, and every gate that builds the working tree agrees with them. The
# pushed commit does not compile for anybody else.
#
# Measured on this branch, 2026-08-31 -- two commits, hours apart, both this shape:
#   15b32ab0  crates/er-save-suppress/src/save_state_witness.rs referencing
#             er_game_base::mem::game_rva_for_hook, er_game_base::rva::SL_LOAD_POLL_WRAPPER_RVA
#             and SL_SAVE_LANE_WRAPPER_RVA -- none of which existed in that commit's er-game-base.
#   11af0c60  boot_progress.rs referencing the er_boot_background and er_cover_fade crates, which
#             were not in the tree at all (not even as unlisted directories).
# a210af7f then landed an 18-file compile closure and made the branch green again. In between,
# `origin` did not compile either, and a dozen agents built on top of it.
#
# HOW IT ANSWERS THE RIGHT QUESTION: it type-checks a git WORKTREE pinned to the commit under
# test, so the only files in scope are the ones actually in that commit. An uncommitted producer
# sitting in the author's checkout is invisible to it, which is the whole point.
#
# WHAT IT COMPILES, and why every word of that invocation is load-bearing:
#   cargo xwin check --workspace --all-targets --keep-going --target x86_64-pc-windows-msvc
#   * --workspace, because the workspace sets `default-members = ["crates/er-quickload"]`. A bare
#     `cargo xwin check`/`build` selects that one package, exits 0 in a fraction of a second having
#     compiled nothing else, and reads exactly like a successful incremental build. That bare form
#     is what scripts/ci-local-check.sh ends with, and it is decorative for this purpose:
#     er-save-suppress is a workspace member that nothing in default-members reaches.
#   * --all-targets, so `#[cfg(test)]` modules, benches and examples are compiled too. A lib-only
#     check reports OK over a test module that names a helper the commit does not carry -- the
#     same defect one layer down. MEASURED 2026-08-31 on a cold cache: 100 s with --all-targets
#     against 105 s without, and 3.1 GB against 2.6 GB. The dev-dependency graph is almost
#     entirely shared with the normal one, so the wider check is free.
#   * the windows target, because most crates here are `#![cfg(windows)]` -- a host `cargo check`
#     compiles them to an empty crate and then reports OK over nothing. And a host check is not a
#     substitute for a different reason too: `cargo check --workspace --all-targets` on the HOST
#     fails at a green HEAD (er-invasion-path, windows-future), because this workspace is not
#     meant to build for Linux as a whole. Measured the same day. Do not "fix" that by adding it.
#   * --keep-going, so one broken crate does not hide the state of the rest. Without it the run
#     stops at er-save-suppress and never reaches er-quickload, so the report understates the
#     damage.
#
# NO BYPASS. There is no --force, no skip flag and no environment escape, by design: the other
# gates in this repo have none either, and a compile gate that can be waved through is the gate
# that was not running in the first place.
#
# Usage:
#   scripts/check-committed-compiles.sh [<rev>...]        # default: HEAD
#   scripts/check-committed-compiles.sh --selftest        # prove the gate can go red
#
# Env:
#   ER_COMMITTED_CHECK_WORKTREE   worktree path   (default <repo>/.worktrees/committed-compiles)
#   ER_COMMITTED_CHECK_TARGET_DIR CARGO_TARGET_DIR (default <repo>/target/committed-compiles)
# Both default inside gitignored directories. The worktree and target dir are REUSED between runs
# on purpose: cargo fingerprints include the absolute source path, so a fresh directory per run is
# a cold build every time (~10 min here) while a stable one is incremental (~seconds when the
# commit under test is close to the last one checked). Concurrent runs serialise on a flock rather
# than racing each other's checkout.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
target="x86_64-pc-windows-msvc"
worktree="${ER_COMMITTED_CHECK_WORKTREE:-$repo_root/.worktrees/committed-compiles}"
target_dir="${ER_COMMITTED_CHECK_TARGET_DIR:-$repo_root/target/committed-compiles}"

selftest=0
revs=()
for arg in "$@"; do
	case "$arg" in
		--selftest) selftest=1 ;;
		-*) echo "[committed-compiles] unknown flag: $arg" >&2; exit 2 ;;
		*) revs+=("$arg") ;;
	esac
done
[[ ${#revs[@]} -eq 0 ]] && revs=(HEAD)

# --- serialise -------------------------------------------------------------------------------
mkdir -p "$(dirname -- "$worktree")" "$target_dir"
exec 9>"$target_dir/.gate.lock"
flock 9

# --- the sibling checkout and the vendored C the workspace cannot load without -----------------
# The root crate uses `../fromsoftware-rs` PATH dependencies, resolved relative to the manifest.
# From <repo>/.worktrees/committed-compiles that is <repo>/.worktrees/fromsoftware-rs, which does
# not exist -- so without this link cargo cannot even parse the workspace, and the gate would fail
# for a reason that has nothing to do with the commit under test.
link_sibling() {
	local real="$repo_root/../fromsoftware-rs" link
	link="$(dirname -- "$worktree")/fromsoftware-rs"
	if [[ ! -d "$real" ]]; then
		echo "[committed-compiles] FAIL: no sibling checkout at $real" >&2
		echo "  the workspace uses ../fromsoftware-rs path dependencies and cannot be loaded without it" >&2
		exit 2
	fi
	# Only ever replace a symlink of our own making; never touch a real directory.
	if [[ -L "$link" || ! -e "$link" ]]; then
		ln -sfn "$(cd -- "$real" && pwd)" "$link"
	fi
}

link_vendor() {
	# vendor/ is gitignored, so a worktree starts without MinHook while the checkout it was made
	# from already has it. Same self-heal scripts/ci-local-check.sh does, for the same reason.
	if [[ ! -f "$worktree/vendor/minhook/src/buffer.c" ]]; then
		if [[ -f "$repo_root/vendor/minhook/src/buffer.c" ]]; then
			mkdir -p "$worktree/vendor"
			ln -sfn "$repo_root/vendor/minhook" "$worktree/vendor/minhook"
		else
			echo "[committed-compiles] FAIL: no vendor/minhook in $repo_root" >&2
			echo "  git clone --depth 1 --branch v1.3.4 https://github.com/TsudaKageyu/minhook.git vendor/minhook" >&2
			exit 2
		fi
	fi
}

# --- pin the worktree to one commit -----------------------------------------------------------
pin_worktree() {
	local sha=$1
	if [[ -d "$worktree/.git" || -f "$worktree/.git" ]]; then
		# `--force` twice: the first lets the detached checkout move even when the previous run
		# left the tree dirty, the second lets it discard an untracked file that a tracked file
		# in the target commit wants to occupy.
		git -C "$worktree" checkout --detach --force --force "$sha" >/dev/null 2>&1 ||
			{ git worktree remove --force "$worktree" >/dev/null 2>&1 || rm -rf -- "$worktree"
			  git -C "$repo_root" worktree add --detach --force "$worktree" "$sha" >/dev/null; }
	else
		rm -rf -- "$worktree"
		git -C "$repo_root" worktree prune >/dev/null 2>&1 || true
		git -C "$repo_root" worktree add --detach --force "$worktree" "$sha" >/dev/null
	fi
	# Remove leftovers from the previous commit under test so a deleted file cannot linger and
	# make a broken commit look whole. -x because the interesting leftovers (a stray crate
	# directory, a generated module) are exactly the gitignored/untracked ones. CARGO_TARGET_DIR
	# lives OUTSIDE the worktree, so this never touches the build cache.
	git -C "$worktree" clean -qxfd
	link_vendor
}

run_one() {
	local rev=$1 sha subject status
	sha=$(git -C "$repo_root" rev-parse --verify "$rev^{commit}")
	subject=$(git -C "$repo_root" log -1 --format=%s "$sha")
	echo "[committed-compiles] $rev -> ${sha:0:8}  $subject"
	pin_worktree "$sha"
	echo "[committed-compiles] cargo xwin check --workspace --all-targets --keep-going --target $target"
	status=0
	( cd "$worktree" && CARGO_TARGET_DIR="$target_dir" \
		cargo xwin check --workspace --all-targets --keep-going --target "$target" ) || status=$?
	if [[ "$status" != 0 ]]; then
		echo "[committed-compiles] FAIL: commit ${sha:0:8} does not compile (exit $status)" >&2
		return 1
	fi
	echo "[committed-compiles] ok: ${sha:0:8}"
	return 0
}

# --- a deliberately broken commit, built without touching any working tree -------------------
# Used by the selftest when the historical failures below are no longer reachable (a squash-merge,
# a shallow clone). It appends a call to a symbol that does not exist onto a LEAF crate -- the same
# error class as the real failures, E0433/E0425 -- and assembles the commit with plumbing against a
# temporary index, so the main checkout, its index and its five agents' uncommitted work are never
# touched. The result is a dangling commit object; `git gc` reaps it.
synth_broken_rev() {
	local base=$1 tmp_index src_path blob tree orig
	src_path=crates/er-enemynpc-effects/src/lib.rs
	tmp_index=$(mktemp "${TMPDIR:-/tmp}/er-committed-compiles-index.XXXXXX")
	rm -f -- "$tmp_index"
	GIT_INDEX_FILE="$tmp_index" git -C "$repo_root" read-tree "$base^{tree}"
	orig=$(git -C "$repo_root" show "$base:$src_path")
	blob=$(printf '%s\npub fn __committed_compiles_selftest() { er_game_base::mem::__no_such_symbol(); }\n' \
		"$orig" | git -C "$repo_root" hash-object -w --stdin)
	GIT_INDEX_FILE="$tmp_index" git -C "$repo_root" update-index --add --cacheinfo "100644,$blob,$src_path"
	tree=$(GIT_INDEX_FILE="$tmp_index" git -C "$repo_root" write-tree)
	rm -f -- "$tmp_index"
	git -C "$repo_root" commit-tree "$tree" -p "$base" -m 'committed-compiles selftest: deliberately broken'
}

link_sibling

# --- selftest ---------------------------------------------------------------------------------
# A gate is not trusted on its own say-so, and "it passed" is worthless from a gate that cannot
# fail. Two-sided: a broken commit must go RED and HEAD must go GREEN, because a gate wedged red
# is as useless as one wedged green.
#
# The red half prefers the two REAL historical failures over a synthetic mutant -- they are the
# commits this gate exists for, they are free, and they exercise the exact shapes seen in the
# wild. They stop being reachable after a squash-merge or in a shallow clone, so the synthetic
# path above takes over rather than letting the selftest quietly pass on nothing.
if [[ "$selftest" == 1 ]]; then
	proved=0
	for bad in 15b32ab0 11af0c60; do
		git -C "$repo_root" rev-parse --verify --quiet "$bad^{commit}" >/dev/null || continue
		if run_one "$bad" >/dev/null 2>&1; then
			echo "[committed-compiles] SELFTEST FAIL: known-broken commit $bad compiled clean" >&2
			exit 1
		fi
		echo "[committed-compiles] selftest: historical failure $bad went red, as it must"
		proved=$((proved + 1))
	done
	if [[ "$proved" == 0 ]]; then
		synth=$(synth_broken_rev HEAD)
		if run_one "$synth" >/dev/null 2>&1; then
			echo "[committed-compiles] SELFTEST FAIL: a synthesised broken tree compiled clean" >&2
			exit 1
		fi
		echo "[committed-compiles] selftest: no historical failure reachable; synthesised ${synth:0:8} went red"
	fi
	if ! run_one HEAD >/dev/null 2>&1; then
		echo "[committed-compiles] SELFTEST FAIL: HEAD does not compile, so a red verdict proves nothing" >&2
		exit 1
	fi
	echo "[committed-compiles] selftest: HEAD went green, as it must"
	echo "[committed-compiles] selftest passed"
	exit 0
fi

failed=()
for rev in "${revs[@]}"; do
	run_one "$rev" || failed+=("$rev")
done

if [[ ${#failed[@]} -ne 0 ]]; then
	cat >&2 <<EOF

[committed-compiles] REFUSING: ${#failed[@]} commit(s) do not compile: ${failed[*]}

This is the "consumer without its producer" shape. Your working tree compiles because it still
holds the file the commit is missing. Find it with:

    git status --short                 # the producer is probably an untracked or modified file here
    git show --stat <rev>              # what the commit actually contains

then amend or add a follow-up commit that carries the closure, and run this gate again.
EOF
	exit 1
fi
