#!/usr/bin/env bash
# Prove scripts/hooks/pre-push refuses, from an agent worktree, a push whose check.sh and ledger
# disagree -- although it still skips check.sh itself there.
#
# The defect this pins, measured 2026-11-25. Commit e98bb2a4 was pushed from a
# `.claude/worktrees/agent-*` checkout with a new check.sh line,
#     python3 "$repo_root/scripts/cupcake_ci_running_claim.py" --selftest
# and no row for it in docs/ci-gate-portability.tsv. The hook skipped the whole suite, as it does in
# every agent worktree, and CI's suite stage went red on ci-gate-portability.py --check,
# check-stages.py --check and check-stage-report.py --selftest. The ledger row turned all three
# green.
#
# How it is measured. A fixture repository sits at a path ending `.claude/worktrees/agent-*`, which
# is the hook's own test for an agent worktree, and carries this repository's real check.sh, real
# ledger and the real three gate scripts, copied in. Every script the hook calls before that branch
# is stubbed to pass. Then the same e98bb2a4 edit is committed in the fixture and pushed through the
# real hook, both as the checkout's HEAD and as another branch's tip, which the hook reads out of
# the commit rather than off the working tree.
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

tmp=$(mktemp -d "${TMPDIR:-/tmp}/er-pre-push-agent-ledger.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
tmp=$(cd -- "$tmp" && pwd -P)

# This suite is itself run by the pre-push hook, and in a linked worktree git exports GIT_DIR to
# that hook. A `git init` that inherits it builds no fixture and edits this repository instead.
git_clean=(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_COMMON_DIR git)

fixture="$tmp/.claude/worktrees/agent-ledger-fixture"
mkdir -p "$(dirname -- "$fixture")"
"${git_clean[@]}" init -q -b work "$fixture"
"${git_clean[@]}" -C "$fixture" config user.email selftest@example.invalid
"${git_clean[@]}" -C "$fixture" config user.name selftest
"${git_clean[@]}" -C "$fixture" config commit.gpgsign false

# The gates under test, and what they read, as they are in this checkout. check-comment-caps.py is
# the comment scanner check-stages.py --audit-inputs loads to strip prose from the gate scripts it
# reads; the gate scripts themselves are absent here, so that audit has fewer files to read than in
# a real checkout, which is not what this test measures.
mkdir -p "$fixture/scripts" "$fixture/docs"
for rel in scripts/check.sh scripts/ci-gate-portability.py scripts/check-stages.py \
	scripts/check-stage-report.py scripts/check-comment-caps.py docs/ci-gate-portability.tsv; do
	cp -f "$repo_root/$rel" "$fixture/$rel"
done
# Every script the hook calls before the agent-worktree branch, stubbed to pass.
# scripts/test-git-pre-push-block-main.sh, test-pre-push-dirty-tree.sh and
# check-runtime-evidence.sh --selftest prove those steps themselves. Exit 3 from the scope tool is
# "nothing cargo compiles changed", so the compile gate is not reached.
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/git-pre-push-block-main.sh"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/check-runtime-evidence.sh"
printf 'import sys\nsys.exit(3 if "--rust-touched" in sys.argv else 0)\n' >"$fixture/scripts/er-change-scope.py"
"${git_clean[@]}" -C "$fixture" add -A
"${git_clean[@]}" -C "$fixture" commit -q -m 'consistent: check.sh and its ledger agree'
sha_clean=$("${git_clean[@]}" -C "$fixture" rev-parse HEAD)

# The e98bb2a4 edit: one new check.sh step, no ledger row.
# shellcheck disable=SC2016  # literal check.sh source text
anchor='python3 "$repo_root/scripts/test-cupcake-stop-guards.py"'
# shellcheck disable=SC2016
new_step='python3 "$repo_root/scripts/cupcake_ci_running_claim.py" --selftest'
if ! ANCHOR=$anchor NEW=$new_step awk '
	{ print }
	$0 == ENVIRON["ANCHOR"] { print ENVIRON["NEW"]; n++ }
	END { exit n != 1 }' "$repo_root/scripts/check.sh" >"$fixture/scripts/check.sh"; then
	bad "check.sh no longer has the line $anchor exactly once; pick another anchor"
fi
"${git_clean[@]}" -C "$fixture" commit -q -am 'the defect: a check.sh step with no ledger row'
sha_defect=$("${git_clean[@]}" -C "$fixture" rev-parse HEAD)

zero=0000000000000000000000000000000000000000
status=0
run_hook() { # run_hook <ref line> -- one push through the hook, as git drives it
	(
		cd "$fixture" || exit 99
		printf '%s\n' "$1" | env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
			ER_PRE_PUSH_NO_CACHE=1 bash "$hook" origin https://example.invalid/fixture.git \
			>"$tmp/out" 2>"$tmp/err"
	)
	status=$?
}
red_gates() { grep -o 'RED python3 [^ ]* [^ ]*' "$tmp/err" | sed 's/^RED python3 //' | sort | tr '\n' ' '; }

# --- the defect, pushed as this checkout's HEAD ---------------------------------------------
run_hook "refs/heads/work $sha_defect refs/heads/work $zero"
if [[ $status -eq 1 ]] && grep -q 'REFUSED -- a check.sh/ledger consistency gate is red' "$tmp/err"; then
	ok "a check.sh step with no ledger row refuses the push from an agent worktree"
else
	bad "the e98bb2a4 defect was let through (exit $status): $(tail -20 "$tmp/err")"
fi
if grep -q "no ledger row for 'cupcake_ci_running_claim.py --selftest'" "$tmp/err"; then
	ok "the refusal carries the gate's own message, naming the step"
else
	bad "the gate's own message is not in the hook's output: $(tail -20 "$tmp/err")"
fi
if grep -q 'agent worktree -- skipping scripts/check.sh' "$tmp/err"; then
	ok "check.sh itself is still skipped in an agent worktree"
else
	bad "the agent-worktree skip did not print: $(tail -5 "$tmp/err")"
fi
# The three gates CI reported red on e98bb2a4. check-stage-report.py --selftest is red here only
# because its fixture is built from the real stage table, where the new step has no stage.
red=$(red_gates)
for gate in "scripts/ci-gate-portability.py --check" "scripts/check-stages.py --check" \
	"scripts/check-stage-report.py --selftest"; do
	if [[ " $red" == *" $gate "* ]]; then
		ok "red, as in CI: $gate"
	else
		bad "not red on the defect, though CI was: $gate (red: $red)"
	fi
done

# --- the same defect as another branch's tip, read out of the commit ------------------------
"${git_clean[@]}" -C "$fixture" branch -q defect "$sha_defect"
"${git_clean[@]}" -C "$fixture" checkout -q --detach "$sha_clean"
run_hook "refs/heads/defect $sha_defect refs/heads/defect $zero"
if [[ $status -eq 1 ]] && grep -q "no ledger row for 'cupcake_ci_running_claim.py --selftest'" "$tmp/err"; then
	ok "a defective tip that is not HEAD is refused too, though the working tree is consistent"
else
	bad "a defective non-HEAD tip was judged by the clean working tree (exit $status): $(tail -20 "$tmp/err")"
fi

# --- a consistent tip passes, HEAD or not -----------------------------------------------------
run_hook "refs/heads/work $sha_clean refs/heads/work $zero"
if [[ $status -eq 0 ]] && grep -q 'ok  python3 scripts/check-stages.py --audit-inputs' "$tmp/err"; then
	ok "a consistent HEAD passes, every gate run including --audit-inputs"
else
	bad "a consistent HEAD was refused (exit $status): $(tail -20 "$tmp/err")"
fi
"${git_clean[@]}" -C "$fixture" checkout -q work

# --- the fix: the ledger row, committed, turns every gate green -----------------------------
printf 'portable\t-\tcupcake_ci_running_claim.py --selftest\tsource/text only\tpolicy\n' \
	>>"$fixture/docs/ci-gate-portability.tsv"
"${git_clean[@]}" -C "$fixture" commit -q -am 'the fix: its ledger row'
sha_fixed=$("${git_clean[@]}" -C "$fixture" rev-parse HEAD)
run_hook "refs/heads/work $sha_fixed refs/heads/work $zero"
if [[ $status -eq 0 && -z $(red_gates) ]]; then
	ok "with the ledger row added, the same push passes"
else
	bad "the fixed commit was still refused (exit $status, red: $(red_gates)): $(tail -20 "$tmp/err")"
fi
"${git_clean[@]}" -C "$fixture" checkout -q --detach "$sha_defect"
run_hook "refs/heads/work $sha_fixed refs/heads/work $zero"
if [[ $status -eq 0 ]] && grep -q 'audit-inputs NOT run -- this tip is not HEAD' "$tmp/err"; then
	ok "a consistent tip that is not HEAD passes, and says --audit-inputs did not run on it"
else
	bad "a consistent non-HEAD tip was refused or did not report the skip (exit $status): $(tail -20 "$tmp/err")"
fi

if [[ $fail -ne 0 ]]; then
	printf '[test-pre-push-agent-worktree-ledger] FAILED\n' >&2
	exit 1
fi
printf '[test-pre-push-agent-worktree-ledger] passed\n'
