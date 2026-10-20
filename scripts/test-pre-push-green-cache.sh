#!/usr/bin/env bash
# Prove scripts/hooks/pre-push does not re-run a tip that already passed, and still runs a tip that
# failed, a tip whose marker expired, and any tip when the cache is switched off.
#
# Why it exists. One red tip refuses the whole push, so on 2026-10-12 a 14-tip push that took about
# two hours left 13 green tips unpushed, and the retry re-ran all 14. The hook now records a green
# tip by its sha and skips it next time.
#
# How it is measured. A fixture repository carries two commits, G (suite passes) and R (suite
# fails), on branches that are not checked out, so both run in the pinned gate worktree. The stub
# check.sh appends the commit it ran on to a log, which is how a skipped run is told apart from a
# run that happened to pass. Every git command names the fixture; markers land in the fixture's own
# git directory.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
hook="$repo_root/scripts/hooks/pre-push"

fail=0
ok() { printf '  ok    %s\n' "$1"; }
bad() {
	printf '  FAIL  %s\n' "$1" >&2
	fail=1
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/er-pre-push-green-cache.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
tmp=$(cd -- "$tmp" && pwd -P)

git_clean=(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_COMMON_DIR git)

fixture="$tmp/fixture"
log="$tmp/suite.log"
"${git_clean[@]}" init -q -b work "$fixture"
"${git_clean[@]}" -C "$fixture" config user.email selftest@example.invalid
"${git_clean[@]}" -C "$fixture" config user.name selftest
"${git_clean[@]}" -C "$fixture" config commit.gpgsign false

mkdir -p "$fixture/scripts"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/git-pre-push-block-main.sh"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/check-committed-compiles.sh"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/check-runtime-evidence.sh"
printf 'import sys\nsys.exit(0)\n' >"$fixture/scripts/er-change-scope.py"
printf 'import sys\nsys.exit(0)\n' >"$fixture/scripts/ci-gate-portability.py"
cat >"$fixture/scripts/check-stages.py" <<'PY'
import sys

print("\n".join(["lint", "policy"] if "--stages" in sys.argv else ["lint"]))
PY
cat >"$fixture/scripts/check.sh" <<'SH'
#!/usr/bin/env bash
printf 'which=%s\n' "$(cat which.txt)" >>"$PRE_PUSH_GREEN_TEST_LOG"
exit "$(cat verdict.txt)"
SH
printf '.worktrees/\n' >"$fixture/.gitignore"
printf 'base\n' >"$fixture/which.txt"
printf '0\n' >"$fixture/verdict.txt"
"${git_clean[@]}" -C "$fixture" add -A
"${git_clean[@]}" -C "$fixture" commit -q -m base

"${git_clean[@]}" -C "$fixture" checkout -q -b green
printf 'G\n' >"$fixture/which.txt"
"${git_clean[@]}" -C "$fixture" commit -q -am G
sha_g=$("${git_clean[@]}" -C "$fixture" rev-parse HEAD)
"${git_clean[@]}" -C "$fixture" checkout -q -b red work
printf 'R\n' >"$fixture/which.txt"
printf '1\n' >"$fixture/verdict.txt"
"${git_clean[@]}" -C "$fixture" commit -q -am R
sha_r=$("${git_clean[@]}" -C "$fixture" rev-parse HEAD)
"${git_clean[@]}" -C "$fixture" checkout -q work

zero=0000000000000000000000000000000000000000
ref_g="refs/heads/green $sha_g refs/heads/green $zero"
ref_r="refs/heads/red $sha_r refs/heads/red $zero"
markers="$fixture/.git/er-pre-push-green"

status=0
run_hook() { # run_hook <ref line>... -- one push through the hook; extra env goes in $hook_env
	: >"$log"
	(
		cd "$fixture" || exit 99
		printf '%s\n' "$@" | env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
			-u ER_PRE_PUSH_NO_CACHE -u ER_SCOPE_ALL -u ER_PRE_PUSH_GREEN_DIR \
			PRE_PUSH_GREEN_TEST_LOG="$log" ER_PRE_PUSH_LOG_DIR="$tmp/logs" "${hook_env[@]}" \
			bash "$hook" origin https://example.invalid/fixture.git >"$tmp/out" 2>"$tmp/err"
	)
	status=$?
}
ran() { grep -qx "which=$1" "$log"; }
hook_env=()

# --- first push: both run, the green one is recorded, the red one is not ---------------------
run_hook "$ref_g" "$ref_r"
if ran G && ran R && [[ $status -ne 0 ]]; then
	ok "a first push runs both tips and R's failure refuses it (exit $status)"
else
	bad "first push: status $status, log: $(cat "$log") $(tail -5 "$tmp/err")"
fi
if [[ -f $markers/$sha_g && $(<"$markers/$sha_g") == lint ]]; then
	ok "the green tip G left a marker naming the stages that ran"
else
	bad "no marker for G under $markers"
fi
if [[ ! -e $markers/$sha_r ]]; then
	ok "the red tip R left no marker"
else
	bad "a failing tip was recorded as green"
fi

# --- the retry: G is skipped, R runs again ---------------------------------------------------
run_hook "$ref_g" "$ref_r"
if ! ran G && ran R && [[ $status -ne 0 ]]; then
	ok "the retry skips G and runs R again"
else
	bad "retry: status $status, log: $(cat "$log")"
fi
if grep -q "${sha_g:0:12} CACHED" "$tmp/err"; then
	ok "the skip is announced on stderr with the sha"
else
	bad "the skip was silent: $(cat "$tmp/err")"
fi

# --- G alone, cached: the push is allowed without running anything --------------------------
run_hook "$ref_g"
if [[ $status -eq 0 && ! -s $log ]]; then
	ok "a push of only cached tips succeeds and runs no suite"
else
	bad "cached-only push: status $status, log: $(cat "$log")"
fi

# --- the off switches ---------------------------------------------------------------------
hook_env=(ER_PRE_PUSH_NO_CACHE=1)
run_hook "$ref_g"
if ran G; then
	ok "ER_PRE_PUSH_NO_CACHE=1 runs a cached tip again"
else
	bad "ER_PRE_PUSH_NO_CACHE=1 still skipped G"
fi
hook_env=(ER_SCOPE_ALL=1)
run_hook "$ref_g"
if ran G; then
	ok "ER_SCOPE_ALL=1 does not accept a marker from a narrowed run"
else
	bad "ER_SCOPE_ALL=1 accepted a narrowed marker"
fi

# --- expiry ---------------------------------------------------------------------------------
hook_env=()
touch -d '25 hours ago' "$markers/$sha_g"
run_hook "$ref_g"
if ran G; then
	ok "a marker older than the default 24h is ignored and G runs again"
else
	bad "an expired marker still skipped G"
fi

if [[ $fail -ne 0 ]]; then
	echo "test-pre-push-green-cache: FAIL" >&2
	exit 1
fi
echo "test-pre-push-green-cache: ok"
