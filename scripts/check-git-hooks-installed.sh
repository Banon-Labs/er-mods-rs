#!/usr/bin/env bash
# IS THE PRE-PUSH GATE ACTUALLY INSTALLED -- BY EITHER OF THE TWO ROUTES GIT CAN TAKE?
#
# Measured 2026-08-31, twice, in the same direction both times: the hook layer failing open while
# looking installed.
#
#   * This clone's `core.hooksPath` was the ABSOLUTE path
#     /home/banon/projects/er-effects-rs/.githooks, left behind by commit 39a919e0, which renamed
#     the repository directory to er-mods-rs. Git resolved its hooks directory to somewhere that no
#     longer existed, so NO hook ran at all -- not the main-push guard, not scripts/ci-local-check.sh
#     -- and nothing said so, because a hook that cannot be found is indistinguishable from a hook
#     that passed.
#
#   * Later the same day `core.hooksPath` was UNSET for about ninety minutes. Git then used its
#     fallback, $GIT_COMMON_DIR/hooks, which is not version-controlled and which held a 537-byte
#     block-main-only pre-push from 2026-07-27 -- no scripts/check-committed-compiles.sh, no
#     scripts/ci-local-check.sh. A push reached origin through it. It happened to be green.
#
# So this asserts, in the order they fail:
#   1. core.hooksPath is set at all;
#   2. it RESOLVES to a real directory holding an executable pre-push;
#   3. the configured value is RELATIVE. An absolute path is correct until the day the checkout
#      moves or is renamed, and then it is silently wrong;
#   4. THE FALLBACK IS SAFE TOO. $GIT_COMMON_DIR/hooks must hold scripts/hooks-fallback-shim
#      verbatim, under every name scripts/hooks/ carries -- so that whichever way git resolves the
#      hook, the same checks run. Checks 1-3 describe a value that several tools write (beads
#      rewrites core.hooksPath: see the header of scripts/hooks-fallback-shim), so the fallback is
#      not a theoretical path. Byte-identical, not merely present: a stale shim is the 2026-07-27
#      failure with a newer date on it.
#
# Not run in CI: a fresh runner has no local hook configuration and does not push.
set -euo pipefail

fail() {
	echo "[check-git-hooks-installed] FAIL: $1" >&2
	cat >&2 <<'FIXEOF'

fix:  bash scripts/install-git-hooks.sh
then: git config --get core.hooksPath        # must print a RELATIVE path: scripts/hooks
FIXEOF
	exit 1
}

check_repo() {
	local root=$1 configured resolved
	# THE STATE THAT ARRIVES WITH IT. Twice on 2026-08-31 -- once observed live, 21 seconds after
	# the fact -- .git/config was rewritten with [core] reduced to exactly the four keys a fresh
	# `git init` writes (repositoryformatversion, filemode, bare, logallrefupdates), with `bare`
	# flipped to true and `hooksPath` GONE, in a SINGLE write, everything below [core] untouched.
	# One writer replacing the whole section, not two `git config` edits; still unattributed (`bd
	# dolt push`, `bd remember` and nested `git worktree add` were each measured innocent). What it
	# looks like from inside is `fatal: this operation must be run in a work tree` out of every git
	# command in the main checkout, which reads like a broken checkout rather than a config key.
	# Name it here so the next person gets a diagnosis instead of a puzzle.
	if [[ -d "$root/.git" ]] && [[ "$(git -C "$root" rev-parse --is-bare-repository 2>/dev/null || echo unknown)" == true ]]; then
		fail "$root has a .git directory but core.bare is true, so git treats this checkout as BARE -- every 'git status' / 'git rev-parse --show-toplevel' in it dies with 'fatal: this operation must be run in a work tree'. Repair with 'git config core.bare false' FIRST, then re-run scripts/install-git-hooks.sh (which no longer dies in this state, but the hooks it installs do). See bd main-checkout-went-bare-config-worktree-is-inert-at-repoformat-0-2026-08-31"
	fi
	configured=$(git -C "$root" config --get core.hooksPath || true)
	[[ -n "$configured" ]] || fail "core.hooksPath is unset in $root, so the version-controlled hooks in scripts/hooks are not installed"
	[[ "$configured" != /* ]] || fail "core.hooksPath is ABSOLUTE ($configured); it breaks the moment this checkout is renamed or moved, which is exactly what happened on 2026-08-31"
	resolved=$(cd -- "$root" && git rev-parse --path-format=absolute --git-path hooks)
	[[ -d "$resolved" ]] || fail "core.hooksPath ($configured) resolves to $resolved, which does not exist -- no hook can run"
	[[ -x "$resolved/pre-push" ]] || fail "$resolved/pre-push is missing or not executable -- nothing gates a push"
	printf '[check-git-hooks-installed] ok -- core.hooksPath=%s -> %s (pre-push executable)\n' \
		"$configured" "$resolved"
	check_fallback "$root"
}

# THE ROUTE GIT TAKES WHEN core.hooksPath IS GONE. Skipped when the checkout carries no shim
# template (an older tree, and the selftest fixtures), because there is then nothing to compare
# against and the shape simply does not exist yet.
check_fallback() {
	local root=$1 template fallback_dir name hook
	template="$root/scripts/hooks-fallback-shim"
	[[ -f "$template" ]] || return 0

	fallback_dir=$(cd -- "$root" && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
	[[ -n "$fallback_dir" ]] || fail "cannot resolve --git-common-dir for $root, so the .git/hooks fallback cannot be verified"
	fallback_dir="$fallback_dir/hooks"
	[[ -d "$fallback_dir" ]] || fail "the fallback hooks directory $fallback_dir does not exist; if core.hooksPath is ever unset, NO hook runs and a push is ungated"

	for hook in "$root"/scripts/hooks/*; do
		[[ -f "$hook" ]] || continue
		name=$(basename -- "$hook")
		[[ -f "$fallback_dir/$name" ]] || fail "$fallback_dir/$name is missing -- with core.hooksPath unset git would run no $name at all"
		[[ -x "$fallback_dir/$name" ]] || fail "$fallback_dir/$name is not executable -- git would skip it and the push would be ungated"
		cmp -s "$template" "$fallback_dir/$name" || fail "$fallback_dir/$name is not scripts/hooks-fallback-shim. A hand-written or stale fallback is how a 537-byte block-main-only pre-push stayed installed for five weeks while the real gate grew around it"
	done
	printf '[check-git-hooks-installed] ok -- fallback %s carries the shim for: %s\n' \
		"$fallback_dir" "$(cd -- "$root/scripts/hooks" && echo *)"
}

# --- selftest ---------------------------------------------------------------------------------
# A gate is not trusted on its own say-so. Rebuild the exact failures in throwaway repos -- an
# absolute hooksPath whose directory has been renamed out from under it, and a fallback directory
# holding a weaker stub than the real hook -- and require a refusal for each.
if [[ "${1:-}" == "--selftest" ]]; then
	# A HOOK'S ENVIRONMENT REDIRECTS EVERY FIXTURE COMMAND BELOW AT THE REAL REPOSITORY, AND THAT
	# IS THE UNATTRIBUTED WRITER: IT IS THIS SCRIPT.
	#
	# git exports GIT_DIR -- and the rest of `git rev-parse --local-env-vars` -- to every hook it
	# runs, and `git -C <dir>` does NOT override GIT_DIR. So under `pre-push` the fixtures below
	# operated on THIS checkout: `git init` re-initialised its git dir, rewriting [core] to exactly
	# the four keys a fresh init writes with `bare` flipped to true and `hooksPath` gone, in a
	# single write, everything below [core] untouched -- the signature described above, down to the
	# key list. The later `config core.hooksPath <abs>` and `config --unset core.hooksPath` lines
	# then rewrote the live hook configuration, which is how the checkout acquired an absolute
	# hooksPath pointing into a deleted /tmp fixture. Every negative control "passed" because the
	# fixture was reading the real repo's correct value, so the gate failed itself, on damage it had
	# just done, once per push.
	#
	# Reproduced deterministically on 2026-08-31:
	#   GIT_DIR=<repo>/.git bash scripts/check-git-hooks-installed.sh --selftest
	#   -> SELFTEST FAIL: a renamed absolute hooksPath was accepted
	#   -> <repo> core.bare=true, core.hooksPath=/tmp/er-hooks-installed-selftest.XXXXXX/before/...
	#
	# shellcheck disable=SC2046  # word splitting is the point: one variable name per word.
	unset $(git rev-parse --local-env-vars)
	# ...AND THEN PROVE IT, because the unset above is only as good as the list it unsets and the
	# mechanism it assumes. MEASURED on git 2.55, twice, with an env-dumping hook at BOTH hook
	# paths (scripts/hooks reached through core.hooksPath, and the fallback directory reached with
	# core.hooksPath unset): a pre-push hook receives GIT_EDITOR, GIT_EXEC_PATH and GIT_PREFIX --
	# and NO GIT_DIR. pre-commit adds GIT_AUTHOR_* and GIT_INDEX_FILE, still no GIT_DIR. So an
	# inherited GIT_DIR is a REAL hazard -- with it exported, the 849cc89b version of this script
	# left this repo's core.hooksPath UNSET and still printed "selftest passed", against a clean
	# control where the config stayed byte-identical -- but it is NOT demonstrated to be how a hook
	# reaches this script, and the comment above overstates that. The snapshot below does not care
	# which mechanism it was: if this selftest changes the ambient config by ANY route, it goes red
	# instead of silently disarming the push gate it exists to protect.
	ambient_config=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/.git/config
	ambient_before=""
	[[ -f "$ambient_config" ]] && ambient_before=$(cat "$ambient_config")

	tmp=$(mktemp -d "${TMPDIR:-/tmp}/er-hooks-installed-selftest.XXXXXX")
	selftest_cleanup() {
		local rc=$?
		rm -rf -- "$tmp"
		if [[ -n "$ambient_before" ]] && [[ "$ambient_before" != "$(cat "$ambient_config")" ]]; then
			echo "[check-git-hooks-installed] SELFTEST FAIL: the selftest MUTATED $ambient_config." >&2
			echo "  Fixture-only work reached the real repository. Look for an inherited git" >&2
			echo "  environment variable, or a git call without -C, and repair the checkout with:" >&2
			echo "      bash scripts/install-git-hooks.sh" >&2
			exit 1
		fi
		exit "$rc"
	}
	trap selftest_cleanup EXIT
	git init -q "$tmp/before"
	mkdir -p "$tmp/before/scripts/hooks"
	printf '#!/usr/bin/env bash\nexit 0\n' >"$tmp/before/scripts/hooks/pre-push"
	chmod 0755 "$tmp/before/scripts/hooks/pre-push"

	git -C "$tmp/before" config core.hooksPath "$tmp/before/scripts/hooks"
	mv "$tmp/before" "$tmp/after" # the rename that broke it
	if "$0" "$tmp/after" >/dev/null 2>&1; then
		echo "[check-git-hooks-installed] SELFTEST FAIL: a renamed absolute hooksPath was accepted" >&2
		exit 1
	fi

	git -C "$tmp/after" config core.hooksPath scripts/hooks # the durable form
	"$0" "$tmp/after" >/dev/null || {
		echo "[check-git-hooks-installed] SELFTEST FAIL: a correct relative hooksPath was rejected" >&2
		exit 1
	}

	git -C "$tmp/after" config --unset core.hooksPath
	if "$0" "$tmp/after" >/dev/null 2>&1; then
		echo "[check-git-hooks-installed] SELFTEST FAIL: an unset hooksPath was accepted" >&2
		exit 1
	fi
	git -C "$tmp/after" config core.hooksPath scripts/hooks

	# --- the fallback half. Give the fixture a shim template, which switches check_fallback on.
	# shellcheck disable=SC2016  # the $(...) and "$@" are the SHIM's text, to be expanded when git
	# runs it, not when this line writes it. Double quotes here would bake this repo's paths into
	# the fixture and the test would stop resembling the shim it is standing in for.
	printf '#!/usr/bin/env bash\nexec bash "$(git rev-parse --show-toplevel)/scripts/hooks/$(basename -- "$0")" "$@"\n' \
		>"$tmp/after/scripts/hooks-fallback-shim"
	chmod 0755 "$tmp/after/scripts/hooks-fallback-shim"

	if "$0" "$tmp/after" >/dev/null 2>&1; then
		echo "[check-git-hooks-installed] SELFTEST FAIL: a missing fallback shim was accepted" >&2
		exit 1
	fi

	# The 2026-07-27 shape: a fallback that exists, is executable, and is WEAKER than the real hook.
	printf '#!/usr/bin/env bash\nexit 0\n' >"$tmp/after/.git/hooks/pre-push"
	chmod 0755 "$tmp/after/.git/hooks/pre-push"
	if "$0" "$tmp/after" >/dev/null 2>&1; then
		echo "[check-git-hooks-installed] SELFTEST FAIL: a weaker hand-written fallback stub was accepted" >&2
		exit 1
	fi

	cp -f "$tmp/after/scripts/hooks-fallback-shim" "$tmp/after/.git/hooks/pre-push"
	chmod 0755 "$tmp/after/.git/hooks/pre-push"
	"$0" "$tmp/after" >/dev/null || {
		echo "[check-git-hooks-installed] SELFTEST FAIL: a correctly installed fallback shim was rejected" >&2
		exit 1
	}

	# A non-executable shim is skipped by git, which is the same hole wearing the right bytes.
	chmod 0644 "$tmp/after/.git/hooks/pre-push"
	if "$0" "$tmp/after" >/dev/null 2>&1; then
		echo "[check-git-hooks-installed] SELFTEST FAIL: a non-executable fallback shim was accepted" >&2
		exit 1
	fi

	chmod 0755 "$tmp/after/.git/hooks/pre-push"

	# ...and the bare flag that came with the unset hooksPath both times it happened.
	git -C "$tmp/after" config core.bare true
	if "$0" "$tmp/after" >/dev/null 2>&1; then
		echo "[check-git-hooks-installed] SELFTEST FAIL: a bare-flagged working checkout was accepted" >&2
		exit 1
	fi
	git -C "$tmp/after" config core.bare false
	"$0" "$tmp/after" >/dev/null || {
		echo "[check-git-hooks-installed] SELFTEST FAIL: a repaired checkout was rejected" >&2
		exit 1
	}

	# THE NEGATIVE CONTROL FOR THE UNSET ABOVE, because the bug it fixes is invisible from inside:
	# with GIT_DIR inherited every assertion still ran, still printed, and still passed the two
	# POSITIVE cases -- only the refusals stopped refusing, and the damage landed somewhere this
	# script never looks. So re-run the whole selftest with GIT_DIR aimed at a repository that must
	# not be touched, and compare its config byte for byte. If the unset ever regresses, the
	# fixtures land in the bystander and this fails instead of the next person's checkout.
	if [[ -z "${ER_HOOKS_SELFTEST_NO_RECURSE:-}" ]]; then
		git init -q "$tmp/bystander"
		bystander_before=$(cat "$tmp/bystander/.git/config")
		if ! GIT_DIR="$tmp/bystander/.git" ER_HOOKS_SELFTEST_NO_RECURSE=1 "$0" --selftest >/dev/null 2>&1; then
			echo "[check-git-hooks-installed] SELFTEST FAIL: the selftest does not survive an inherited GIT_DIR, which is the environment every git hook runs in" >&2
			exit 1
		fi
		if [[ "$bystander_before" != "$(cat "$tmp/bystander/.git/config")" ]]; then
			echo "[check-git-hooks-installed] SELFTEST FAIL: with GIT_DIR inherited the fixtures rewrote the bystander repository's config -- that is the live-checkout corruption, reproduced" >&2
			exit 1
		fi
	fi

	echo "[check-git-hooks-installed] selftest passed"
	exit 0
fi

check_repo "${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
