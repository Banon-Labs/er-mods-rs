#!/usr/bin/env bash
# Prove scripts/hooks/pre-push closes an agent-launched game for an authorized push, leaves any
# other game alone, and hands back the user's `authorize <sha>` reply when it refuses a push for a
# precondition rather than for the pushed content.
#
# The defect this pins, 2026-10-27: the user replied `authorize 6e313ae7...`, the agent pushed with
# its own stamping run (br-20261027-210646-679a) still up, the hook's check.sh refused with
# "Elden Ring is running", nothing reached origin -- and the reply was already recorded as spent by
# scripts/cupcake_push_authorization.py, so the retry and the push teardown were both refused.
#
# How it is measured. The real hook and the real scripts/er-pre-push-clear.py run in a fixture
# repository. scripts/er-teardown.py is a stub whose `--status` reports a game while a marker file
# exists and whose teardown removes the marker and logs its `--reason`; check.sh is a stub that
# refuses like the real one while the marker exists. ER_RUN_STATE_DIR points the launcher's run
# root at the fixture, where a `br-*` run.json names this test's own (live) pid. The spent-
# authorization file is the fixture's own `.cupcake/state/push-authorizations.jsonl`. No real game
# is looked at or touched.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
hook="$repo_root/scripts/hooks/pre-push"

fail=0
ok() { printf '  ok    %s\n' "$1"; }
bad() {
	printf '  FAIL  %s\n' "$1" >&2
	fail=1
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/er-pre-push-authorized-teardown.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
tmp=$(cd -- "$tmp" && pwd -P)

git_clean=(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_COMMON_DIR git)

fixture="$tmp/fixture"
"${git_clean[@]}" init -q -b work "$fixture"
"${git_clean[@]}" -C "$fixture" config user.email selftest@example.invalid
"${git_clean[@]}" -C "$fixture" config user.name selftest
"${git_clean[@]}" -C "$fixture" config commit.gpgsign false

mkdir -p "$fixture/scripts"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/git-pre-push-block-main.sh"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/check-committed-compiles.sh"
# shellcheck disable=SC2016 # the stub expands the variable when it runs, not here
printf '#!/usr/bin/env bash\nexit "$(cat "$PRE_PUSH_TD_EVIDENCE")"\n' >"$fixture/scripts/check-runtime-evidence.sh"
printf 'import sys\nsys.exit(0)\n' >"$fixture/scripts/er-change-scope.py"
printf 'import sys\nsys.exit(0)\n' >"$fixture/scripts/ci-gate-portability.py"
printf 'print("lint")\n' >"$fixture/scripts/check-stages.py"
cp -f "$repo_root/scripts/er-pre-push-clear.py" "$repo_root/scripts/er_run_lib.py" "$fixture/scripts/"
cat >"$fixture/scripts/er-teardown.py" <<'PY'
import os, sys
marker = os.environ["PRE_PUSH_TD_GAME"]
if "--status" in sys.argv:
    print("[er-teardown] game health: " + ("pid=4242 threads=90 -> running" if os.path.exists(marker) else "no eldenring.exe"))
    sys.exit(0)
with open(os.environ["PRE_PUSH_TD_LOG"], "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
if not os.path.exists(os.environ["PRE_PUSH_TD_WEDGED"]) and os.path.exists(marker):
    os.remove(marker)
PY
cat >"$fixture/scripts/check.sh" <<'SH'
#!/usr/bin/env bash
if [[ -e $PRE_PUSH_TD_GAME ]]; then
	echo "check.sh: REFUSED -- Elden Ring is running." >&2
	exit 2
fi
echo suite >>"$PRE_PUSH_TD_SUITE"
exit 0
SH
printf '.worktrees/\n.cupcake/state/\n' >"$fixture/.gitignore"
printf 'one\n' >"$fixture/file.txt"
"${git_clean[@]}" -C "$fixture" add -A
"${git_clean[@]}" -C "$fixture" commit -q -m 'the commit being pushed'
head=$("${git_clean[@]}" -C "$fixture" rev-parse HEAD)

zero=0000000000000000000000000000000000000000
ref_line="refs/heads/work $head refs/heads/work $zero"
state="$fixture/.cupcake/state/push-authorizations.jsonl"
mkdir -p "$fixture/.cupcake/state"

game="$tmp/game-up"
wedged="$tmp/wedged"
tdlog="$tmp/teardown.log"
suite="$tmp/suite.log"
evidence="$tmp/evidence-rc"
runs="$tmp/runs"

agent_run() { # agent_run <pid>: the newest br-* run, launched by <pid>
	rm -rf "$runs"
	mkdir -p "$runs/br-20991027-210646-679a"
	printf '{"run_id": "br-20991027-210646-679a", "pid": %s}\n' "$1" >"$runs/br-20991027-210646-679a/run.json"
}
authorize() { # authorize: the push guard's spend record for this push, written seconds ago
	printf '{"message_uuid": "m-1", "tool_use_id": "toolu_1", "branch": "work", "tip": "%s", "at": %s}\n' \
		"$head" "$(date +%s)" >"$state"
}
last_record() { tail -1 "$state" 2>/dev/null; }

status=0
run_hook() {
	: >"$tdlog"
	: >"$suite"
	(
		cd "$fixture" || exit 99
		printf '%s\n' "$ref_line" | env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u ER_CHECK_FORCE \
			ER_PRE_PUSH_NO_CACHE=1 ER_PRE_PUSH_LOG_DIR="$tmp/logs" ER_RUN_STATE_DIR="$runs" \
			PRE_PUSH_TD_GAME="$game" PRE_PUSH_TD_WEDGED="$wedged" PRE_PUSH_TD_LOG="$tdlog" \
			PRE_PUSH_TD_SUITE="$suite" PRE_PUSH_TD_EVIDENCE="$evidence" \
			bash "$hook" origin https://example.invalid/fixture.git >"$tmp/out" 2>"$tmp/err"
	)
	status=$?
}
echo 0 >"$evidence"

# --- the 2026-10-27 case: agent run up, authorized push -> torn down, suite runs, push passes -----
touch "$game"
agent_run $$
authorize
run_hook
if [[ $status -eq 0 && -s $suite && ! -e $game ]] && grep -qxF -e "--reason=push-${head:0:12}" "$tdlog"; then
	ok "an authorized push closes the agent's own run (--reason=push-<tip12>) and the suite runs"
else
	bad "agent run + authorization: status $status, teardown: $(cat "$tdlog"), err: $(tail -5 "$tmp/err")"
fi

# --- a game no er-run-branch.py run launched: left alone, push refused, reply handed back --------
touch "$game"
agent_run 2147483646 # a pid that is not alive: the br-* run has ended, so the game is someone else's
authorize
run_hook
if [[ $status -ne 0 && -e $game && ! -s $tdlog && ! -s $suite ]]; then
	ok "a game the user launched is not torn down, and the push is refused before the suite"
else
	bad "user game: status $status, teardown: $(cat "$tdlog"), suite: $(cat "$suite")"
fi
if last_record | grep -q '"refunded": "game-running"'; then
	ok "...and the user's authorization is refunded, not spent"
else
	bad "user game: no refund recorded: $(last_record)"
fi

# --- a push with no authorization record (the user pushing from their own terminal) -------------
touch "$game"
agent_run $$
: >"$state"
run_hook
if [[ $status -ne 0 && -e $game && ! -s $tdlog ]]; then
	ok "a push without an authorization record never tears the game down"
else
	bad "unauthorized push: status $status, teardown: $(cat "$tdlog")"
fi

# --- a teardown that does not take (a wedged game refuses): refused and refunded ----------------
touch "$game" "$wedged"
agent_run $$
authorize
run_hook
if [[ $status -ne 0 && -e $game && ! -s $suite ]] && last_record | grep -q refunded; then
	ok "a game the teardown could not close refuses the push and refunds the reply"
else
	bad "wedged: status $status, suite: $(cat "$suite"), last: $(last_record)"
fi
rm -f "$wedged" "$game"

# --- no game: nothing torn down, nothing refunded ------------------------------------------------
authorize
run_hook
if [[ $status -eq 0 && ! -s $tdlog ]] && ! last_record | grep -q refunded; then
	ok "with no game up nothing is torn down and the authorization stays spent"
else
	bad "no game: status $status, teardown: $(cat "$tdlog"), last: $(last_record)"
fi

# --- precondition refusals hand the reply back ---------------------------------------------------
authorize
echo 1 >"$evidence"
run_hook
if [[ $status -ne 0 ]] && last_record | grep -q '"refunded": "no-runtime-evidence"'; then
	ok "a push refused for missing runtime evidence refunds the reply"
else
	bad "evidence: status $status, last: $(last_record)"
fi
echo 0 >"$evidence"

authorize
printf 'two\n' >"$fixture/file.txt"
run_hook
if [[ $status -ne 0 ]] && last_record | grep -q '"refunded": "dirty-tree"'; then
	ok "a push refused for a dirty working tree refunds the reply"
else
	bad "dirty tree: status $status, last: $(last_record)"
fi
"${git_clean[@]}" -C "$fixture" checkout -q -- file.txt

if [[ $fail -ne 0 ]]; then
	echo "test-pre-push-authorized-teardown: FAIL" >&2
	exit 1
fi
echo "test-pre-push-authorized-teardown: ok"
