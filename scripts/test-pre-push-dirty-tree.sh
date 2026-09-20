#!/usr/bin/env bash
# Prove scripts/hooks/pre-push refuses when the gate would read a tree the push is not sending.
#
# The defect this pins, measured 2026-09-20. The hook selects its stages from the PUSHED REFS and
# then runs `scripts/check.sh`, which reads the WORKING TREE. A push of
# `phase2/one-row-shell-config-selected` went out while its author was mid-edit, between adding a
# call and adding the function it calls. check.sh compiled that tree, `cargo-build` failed with
# `E0425: cannot find function ... in this scope`, and the push was refused -- for a defect in
# neither the commit nor the finished tree, after the other ten stages had already spent 400
# seconds on `addresses` alone.
#
# The inverse is the one that matters and is silent: edit a file into a passing state, push a
# commit without it, and the gate reports green about work the remote never receives.
#
# Why a behavioural test and not a grep. What is easy to lose is not the comparison but its
# POSITION: moved below the `exec bash scripts/check.sh` it still greps fine and can never run,
# and moved above the deletion skip it would refuse pushes that carry no tip at all. So this drives
# the real hook with a real repository and a real ref list, and measures the exit status.
#
# Nothing here touches the repository it lives in: every git command names a fixture under a temp
# directory, and the hook is invoked with that fixture as its cwd.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
hook="$repo_root/scripts/hooks/pre-push"

fail=0
ok() { printf '  ok    %s\n' "$1"; }
bad() {
	printf '  FAIL  %s\n' "$1" >&2
	fail=1
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# A fixture repository with one commit on a branch that is not `main`, so the main-branch guard
# near the top of the hook does not answer first and hide what this is measuring.
fixture="$tmp/fixture"
git init -q -b work "$fixture"
git -C "$fixture" config user.email t@t
git -C "$fixture" config user.name t
printf 'one\n' >"$fixture/file.txt"
git -C "$fixture" add file.txt
git -C "$fixture" commit -q -m 'the commit being pushed'
head=$(git -C "$fixture" rev-parse HEAD)

# The hook calls `scripts/git-pre-push-block-main.sh` relative to its repo root before reaching the
# check under test, and that root is this fixture. Stub it rather than symlinking the real
# `scripts/` in: a symlink would put every later gate on the fixture's path too, and the clean-tree
# case below would then run `scripts/check.sh` for real. The stub returns a pass, which is what the
# real guard returns for a branch that is not `main`; scripts/test-git-pre-push-block-main.sh is
# what proves that guard itself.
mkdir -p "$fixture/scripts"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/git-pre-push-block-main.sh"

# The ref list git feeds a pre-push hook on stdin: `<local ref> <local sha> <remote ref> <remote
# sha>`. A zero remote sha is a new branch, which is what this push is.
zero=0000000000000000000000000000000000000000
ref_line="refs/heads/work $head refs/heads/work $zero"

# Run the hook exactly as git does, with the fixture as cwd, and capture what it decided. The
# scrub at the top of the hook unsets the GIT_* variables, so exporting GIT_DIR here mimics git
# without letting the hook write anywhere but the fixture.
run_hook() {
	(
		cd "$fixture" || exit 99
		printf '%s\n' "$ref_line" | bash "$hook" origin https://example.invalid/fixture.git \
			>"$tmp/out" 2>"$tmp/err"
	)
}

# --- clean tree: the refusal must not fire -------------------------------------------------
#
# It must not PASS either -- the hook goes on to run the real gates, which cannot work in a
# fixture. So the assertion is on the message, not on the exit status: a clean tree must never be
# the thing that stopped the push.
run_hook
if grep -q 'the working tree differs from the commit being pushed' "$tmp/err"; then
	bad "a clean tree was refused as dirty"
else
	ok "a clean tree is not refused by this check"
fi

# --- a tracked file edited after the commit: refuse ----------------------------------------
printf 'two\n' >>"$fixture/file.txt"
run_hook
status=$?
if [[ $status -eq 1 ]] && grep -q 'the working tree differs from the commit being pushed' "$tmp/err"; then
	ok "an edited tracked file refuses the push"
else
	bad "an edited tracked file was allowed through (exit $status)"
fi
if grep -q 'file.txt' "$tmp/err"; then
	ok "the refusal names the file that differs"
else
	bad "the refusal did not name file.txt, so nobody can act on it"
fi
git -C "$fixture" checkout -q -- file.txt

# --- an untracked file: do not refuse -------------------------------------------------------
#
# check.sh reads tracked source. An untracked scratch file is not content the push is failing to
# carry, and refusing on one would make the hook unusable during ordinary work.
printf 'scratch\n' >"$fixture/scratch.txt"
run_hook
if grep -q 'the working tree differs from the commit being pushed' "$tmp/err"; then
	bad "an untracked file refused the push"
else
	ok "an untracked file does not refuse the push"
fi
rm -f "$fixture/scratch.txt"

# --- a pushed tip that is not this checkout's HEAD: do not refuse ---------------------------
#
# Pushing some other branch's tip is not a tree this checkout can speak for, so the comparison is
# meaningless and the pre-existing behaviour has to stand.
git -C "$fixture" checkout -q -b other
printf 'three\n' >>"$fixture/file.txt"
git -C "$fixture" commit -q -am 'a commit on another branch'
other_head=$(git -C "$fixture" rev-parse HEAD)
git -C "$fixture" checkout -q work
printf 'dirty\n' >>"$fixture/file.txt"
ref_line="refs/heads/other $other_head refs/heads/other $zero"
run_hook
if grep -q 'the working tree differs from the commit being pushed' "$tmp/err"; then
	bad "a dirty tree refused a push of a tip that is not HEAD"
else
	ok "a tip that is not HEAD is not compared against this working tree"
fi

# --- position: after the deletion skip, before the gate that reads the tree -----------------
refusal_line=$(grep -n 'the working tree differs from the commit being pushed' "$hook" | head -1 | cut -d: -f1)
deletion_line=$(grep -n 'deletion-only push' "$hook" | head -1 | cut -d: -f1)
exec_line=$(grep -n '^exec bash scripts/check.sh' "$hook" | head -1 | cut -d: -f1)
if [[ -n $refusal_line && -n $exec_line && $refusal_line -lt $exec_line ]]; then
	ok "the refusal is at line $refusal_line -- before the gate at $exec_line that reads the tree"
else
	bad "the refusal is not before the gate it protects (refusal=$refusal_line exec=$exec_line)"
fi
if [[ -n $deletion_line && $refusal_line -gt $deletion_line ]]; then
	ok "the refusal is after the deletion skip at $deletion_line, so a deletion never reaches it"
else
	bad "the refusal sits before the deletion skip (refusal=$refusal_line deletion=$deletion_line)"
fi

if [[ $fail -ne 0 ]]; then
	printf '[test-pre-push-dirty-tree] FAILED\n' >&2
	exit 1
fi
printf '[test-pre-push-dirty-tree] passed\n'
