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
	tmp=$(mktemp -d "${TMPDIR:-/tmp}/er-hooks-installed-selftest.XXXXXX")
	trap 'rm -rf -- "$tmp"' EXIT
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

	echo "[check-git-hooks-installed] selftest passed"
	exit 0
fi

check_repo "${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
