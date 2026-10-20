#!/usr/bin/env bash
# Prove scripts/hooks/pre-push runs the suite on the commit being pushed, not on the checkout `git
# push` was typed in (bd er-effects-rs-xe0r).
#
# The defect this pins, measured 2026-10-05: `git -C <main checkout> push -u origin
# feat/ai-simulated-player`, from a checkout that had feat/er-r3-view-rest-of-catalog out. The
# stage selection diffed the pushed ref; check.sh then read the main checkout's working tree, went
# red on a crate the pushed branch does not contain, and would equally have passed code the pushed
# branch breaks.
#
# How it is measured. A fixture repository carries two commits whose stub scripts/check.sh differ:
# each records which commit it is and in which directory it ran, and exits with that commit's own
# verdict. The checkout sits on commit A and pushes commit B through the real hook. A hook that
# gates the checkout records A; a hook that gates the push records B. The verdict half matters as
# much as the record: B's check.sh fails, so the push must fail even though A's would pass.
#
# Nothing here touches the repository it lives in: every git command names a fixture under a temp
# directory, the hook is invoked with that fixture as its cwd, and the gate worktree the hook pins
# lands under the fixture.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
hook="$repo_root/scripts/hooks/pre-push"

fail=0
ok() { printf '  ok    %s\n' "$1"; }
bad() {
	printf '  FAIL  %s\n' "$1" >&2
	fail=1
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/er-pre-push-gates-pushed-ref.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
tmp=$(cd -- "$tmp" && pwd -P)

# This suite is itself run by the pre-push hook, and in a linked worktree git exports GIT_DIR to
# that hook. A `git init` that inherits it builds no fixture and edits this repository instead.
git_clean=(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_COMMON_DIR git)

fixture="$tmp/fixture"
gate="$fixture/.worktrees/pre-push-gate"
log="$tmp/suite.log"
logs="$tmp/pre-push-logs"
"${git_clean[@]}" init -q -b work "$fixture"
"${git_clean[@]}" -C "$fixture" config user.email selftest@example.invalid
"${git_clean[@]}" -C "$fixture" config user.name selftest
"${git_clean[@]}" -C "$fixture" config commit.gpgsign false

# Every script the hook calls before the suite, stubbed to pass, so what is measured is only which
# tree the suite runs in. scripts/test-pre-push-deletion-only.sh, -dirty-tree.sh and
# test-git-pre-push-block-main.sh prove those earlier steps themselves.
mkdir -p "$fixture/scripts"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/git-pre-push-block-main.sh"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/check-committed-compiles.sh"
printf '#!/usr/bin/env bash\nexit 0\n' >"$fixture/scripts/check-runtime-evidence.sh"
printf 'import sys\nsys.exit(0)\n' >"$fixture/scripts/er-change-scope.py"
cat >"$fixture/scripts/ci-gate-portability.py" <<'PY'
import sys

# The one gitignored input this fixture has: it must reach the gate worktree by a link.
if "--root-inputs" in sys.argv:
    print("game-image.bin")
PY
cat >"$fixture/scripts/check-stages.py" <<'PY'
import sys

# A narrowed selection, so the hook takes its ER_CHECK_STAGES branch.
print("\n".join(["lint", "policy"] if "--stages" in sys.argv else ["lint"]))
PY
# The stub also behaves like the real check.sh towards its result directory: it clears the stage
# logs it finds there and writes its own, so a hook that hands every tip the same directory loses
# the earlier tip's log exactly the way the real one did.
cat >"$fixture/scripts/check.sh" <<'SH'
#!/usr/bin/env bash
printf 'tree=%s head=%s which=%s stages=%s input=%s results=%s\n' \
	"$(pwd -P)" "$(git rev-parse HEAD)" "$(cat which.txt)" "${ER_CHECK_STAGES:-}" \
	"$(cat game-image.bin 2>/dev/null || echo absent)" "${ER_CHECK_RESULT_DIR:-}" \
	>>"$PRE_PUSH_GATES_TEST_LOG"
if [[ -n ${ER_CHECK_RESULT_DIR:-} ]]; then
	mkdir -p "$ER_CHECK_RESULT_DIR"
	rm -f "$ER_CHECK_RESULT_DIR"/*.log
	printf 'stage log of %s\n' "$(cat which.txt)" >"$ER_CHECK_RESULT_DIR/addresses.log"
fi
exit "$(cat verdict.txt)"
SH
printf 'game-image.bin\n.worktrees/\n' >"$fixture/.gitignore"
printf 'A\n' >"$fixture/which.txt"
printf '0\n' >"$fixture/verdict.txt"
"${git_clean[@]}" -C "$fixture" add -A
"${git_clean[@]}" -C "$fixture" commit -q -m 'A: the checked-out commit, whose suite passes'
sha_a=$("${git_clean[@]}" -C "$fixture" rev-parse HEAD)

"${git_clean[@]}" -C "$fixture" checkout -q -b other
printf 'B\n' >"$fixture/which.txt"
printf '1\n' >"$fixture/verdict.txt"
"${git_clean[@]}" -C "$fixture" commit -q -am 'B: the pushed commit, whose suite fails'
sha_b=$("${git_clean[@]}" -C "$fixture" rev-parse HEAD)
"${git_clean[@]}" -C "$fixture" checkout -q work

# Present in the checkout, ignored by git, absent from any fresh worktree.
printf 'the-image\n' >"$fixture/game-image.bin"

zero=0000000000000000000000000000000000000000
status=0
hook_under_test=$hook
run_hook() { # run_hook <ref line>... -- one push through the hook, as git drives it
	: >"$log"
	(
		cd "$fixture" || exit 99
		printf '%s\n' "$@" | env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
			PRE_PUSH_GATES_TEST_LOG="$log" ER_PRE_PUSH_LOG_DIR="$logs" ER_PRE_PUSH_NO_CACHE=1 bash "$hook_under_test" origin \
			https://example.invalid/fixture.git >"$tmp/out" 2>"$tmp/err"
	)
	status=$?
}
line_for() { grep "which=$1 " "$log" | head -1; }
ref_a="refs/heads/work $sha_a refs/heads/work $zero"
ref_b="refs/heads/other $sha_b refs/heads/other $zero"

# --- the defect: push B from a checkout on A ------------------------------------------------
run_hook "$ref_b"
rec=$(line_for B)
if [[ -n $rec && $rec == *"head=$sha_b "* ]]; then
	ok "pushing B from a checkout on A runs the suite on B"
else
	bad "pushing B from a checkout on A did not run the suite on B: $(cat "$log") $(tail -5 "$tmp/err")"
fi
if [[ -z $(line_for A) ]]; then
	ok "the checkout's own commit A was not what the suite read"
else
	bad "the suite read the pushing checkout (A) instead of the pushed commit: $(cat "$log")"
fi
if [[ $rec == *"tree=$gate "* ]]; then
	ok "it ran in the pinned gate worktree $gate"
else
	bad "it did not run in $gate: $rec"
fi
if [[ $status -ne 0 ]]; then
	ok "B's own failing verdict refuses the push, though A's suite would have passed (exit $status)"
else
	bad "the push of B was allowed, so the verdict was not B's"
fi
if [[ $rec == *"stages=lint "* ]]; then
	ok "stage selection still narrows the run (ER_CHECK_STAGES=lint)"
else
	bad "stage selection did not reach the suite: $rec"
fi
if [[ $rec == *"input=the-image"* ]]; then
	ok "the checkout's gitignored input was linked into the gate worktree"
else
	bad "the gitignored input did not reach the gate worktree: $rec"
fi
if [[ $("${git_clean[@]}" -C "$fixture" rev-parse HEAD) == "$sha_a" && $(cat "$fixture/which.txt") == A ]]; then
	ok "the pushing checkout was left on A, untouched"
else
	bad "the pushing checkout was moved or edited"
fi

# --- dirt in the pushing checkout cannot reach the pushed commit's run ----------------------
printf 'dirty\n' >"$fixture/which.txt"
touch "$gate/stray-from-a-previous-run.txt"
mkdir -p "$gate/target"
touch "$gate/target/cache-entry"
run_hook "$ref_b"
rec=$(line_for B)
if [[ -n $rec && $rec == *"head=$sha_b "* ]]; then
	ok "a dirty checkout on A still gates B, and B's tree is clean of that dirt"
else
	bad "a dirty checkout leaked into the run for B: $(cat "$log")"
fi
"${git_clean[@]}" -C "$fixture" checkout -q -- which.txt
if [[ ! -e $gate/stray-from-a-previous-run.txt ]]; then
	ok "a file left in the gate worktree by a previous run is removed before the next"
else
	bad "a leftover file survived the pin, so a deleted file could make a broken commit look whole"
fi
if [[ -e $gate/target/cache-entry ]]; then
	ok "the gate worktree's target/ survives the pin, so cargo stays incremental"
else
	bad "the pin wiped target/, so every push would be a cold build"
fi

# --- a push of `HEAD` still runs in place --------------------------------------------------
run_hook "$ref_a"
rec=$(line_for A)
if [[ -n $rec && $rec == *"tree=$fixture "* && $rec == *"head=$sha_a "* && $status -eq 0 ]]; then
	ok "pushing the checkout's own HEAD runs in the checkout, as before (exit $status)"
else
	bad "pushing HEAD did not run in place: status $status, $(cat "$log")"
fi

# --- two refs in one push: each tip gated in its own tree ----------------------------------
run_hook "$ref_a" "$ref_b"
rec_a=$(line_for A)
rec_b=$(line_for B)
if [[ $rec_a == *"tree=$fixture "* && $rec_b == *"tree=$gate "* && $status -ne 0 ]]; then
	ok "a two-ref push gates A in place and B in the worktree, and B's failure fails the push"
else
	bad "a two-ref push was not gated per tip: status $status, $(cat "$log")"
fi

# --- ...and each tip's stage logs survive the next tip's run --------------------------------
#
# The defect, measured 2026-10-08: every tip's check.sh wrote to the one shared result directory,
# so a red tip's logs were deleted by the next tip's run before anyone read them.
if [[ $rec_a == *"results=$logs/$sha_a"* && $rec_b == *"results=$logs/$sha_b"* ]]; then
	ok "each tip's suite is handed its own result directory, keyed by its sha"
else
	bad "the tips were not given per-sha result directories: $(cat "$log")"
fi
if [[ $(cat "$logs/$sha_a/addresses.log" 2>/dev/null) == "stage log of A" &&
	$(cat "$logs/$sha_b/addresses.log" 2>/dev/null) == "stage log of B" ]]; then
	ok "both tips' stage logs are still on disk after the push"
else
	bad "a tip's stage log was lost to a later tip: $(ls -R "$logs" 2>&1)"
fi
if grep -q "$logs/$sha_b/" "$tmp/err" && ! grep -q "  ${sha_a:0:12}  $logs/" "$tmp/err"; then
	ok "the closing summary names the failing tip's log directory, and only that one"
else
	bad "the closing summary did not name B's logs alone: $(tail -5 "$tmp/err")"
fi

# --- the gate worktree's lock -------------------------------------------------------------
#
# Two pushes of two different branches would otherwise pin the same directory under each other.
(
	flock -n 9 || exit 98
	run_hook "$ref_b"
	exit "$status"
) 9>"$gate.lock"
held_status=$?
if [[ $held_status -eq 1 ]] && grep -q 'another push is already gating' "$tmp/err" && [[ ! -s $log ]]; then
	ok "a held gate-worktree lock refuses the push before anything runs"
else
	bad "a held gate-worktree lock did not refuse (status $held_status): $(cat "$tmp/err")"
fi

# --- negative control ----------------------------------------------------------------------
#
# Without this, the cases above would pass on any fixture where the stub happened to record B. So
# the same push runs through a copy of the hook whose "is this tip HEAD" test is replaced by
# `false` -- the hook as it was before 2026-10-05, gating whatever is checked out -- and that copy
# must record A. Both occurrences must be replaced, or the control is measuring a half-edited hook.
# shellcheck disable=SC2016  # a regex matching the hook's literal source text, not an expansion.
not_head_test='\[\[ -n \$sha && \$sha != "\$head_sha" \]\]'
occurrences=$(grep -c "$not_head_test" "$hook")
sed "s/$not_head_test/false/" "$hook" >"$tmp/pre-push-gating-the-checkout"
if [[ $occurrences -eq 2 ]]; then
	hook_under_test="$tmp/pre-push-gating-the-checkout"
	run_hook "$ref_b"
	hook_under_test=$hook
	if [[ -n $(line_for A) && -z $(line_for B) ]]; then
		ok "negative control: a hook that gates the checkout records A for the same push of B"
	else
		bad "negative control did not record A -- this suite proves nothing: $(cat "$log")"
	fi
else
	bad "negative control: expected the not-HEAD test twice in the hook, found $occurrences"
fi

if [[ $fail -ne 0 ]]; then
	printf '[test-pre-push-gates-pushed-ref] FAILED\n' >&2
	exit 1
fi
printf '[test-pre-push-gates-pushed-ref] passed\n'
