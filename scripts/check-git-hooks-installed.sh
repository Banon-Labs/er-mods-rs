#!/usr/bin/env bash
# IS THE PRE-PUSH GATE ACTUALLY INSTALLED, AND INSTALLED IN A WAY THAT SURVIVES A RENAME?
#
# Measured 2026-08-31: this clone's `core.hooksPath` was the ABSOLUTE path
# /home/banon/projects/er-effects-rs/.githooks, left behind by commit 39a919e0, which renamed the
# repository directory to er-mods-rs. Git resolved its hooks directory to somewhere that no longer
# existed, so NO hook ran at all -- not the main-push guard, not scripts/ci-local-check.sh -- and
# nothing said so, because a hook that cannot be found is indistinguishable from a hook that
# passed. That is the same defect every gate here exists to refuse, applied to the gates
# themselves.
#
# So this asserts three things, in the order they fail:
#   1. core.hooksPath is set at all (an unset value falls back to .git/hooks, which is NOT the
#      version-controlled hook set and carries only a partial copy of the main-push guard);
#   2. it RESOLVES to a real directory holding an executable pre-push (the rot above);
#   3. the configured value is RELATIVE. An absolute path is correct until the day the checkout
#      moves or is renamed, and then it is silently wrong. `scripts/install-git-hooks.sh` already
#      writes a relative value; the broken one had been set by hand.
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
}

# --- selftest ---------------------------------------------------------------------------------
# A gate is not trusted on its own say-so. Rebuild the exact failure in a throwaway repo -- an
# absolute hooksPath whose directory has been renamed out from under it -- and require a refusal.
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
	echo "[check-git-hooks-installed] selftest passed"
	exit 0
fi

check_repo "${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
