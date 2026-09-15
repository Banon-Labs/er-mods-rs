#!/usr/bin/env bash
# Prove scripts/hooks/pre-push runs no gate on a push that only deletes refs, and every gate on
# one that does not.
#
# The defect this pins, measured 2026-09-14. `git push origin --delete refactor/experiments-split`
# from a checkout sitting on an unrelated fix branch printed
#     check-stages: 7 of 11 stage(s) selected from 6 changed path(s).
# and the deletion never completed. Those six paths were the checked-out branch's own commits:
# with no `--rev` the stage selector falls back to the working tree against origin/main, and
# `check-runtime-evidence.sh` with no tip falls back to `git rev-parse --short HEAD`. Both gated a
# tree the push was not sending -- the shape PR #434 named, in its purest form, because a deletion
# sends no commit for a content gate to read.
#
# Why the fixture is built rather than the ref lists typed. The whole decision turns on bytes this
# repo does not author: git writes `(delete)` in field 1 and forty zeros in field 2 for a deleted
# ref, and a test that writes those characters itself proves the hook parses its own author's
# guess. So this builds a bare remote plus a working clone under mktemp, installs the real hook
# through `core.hooksPath`, and drives it with real `git push` invocations -- the first case does
# nothing but record and assert the ref line git actually produces.
#
# Why the gates are stubs. The hook ends in `exec bash scripts/check.sh`, which is a half-hour
# suite that refuses to run in an agent worktree by design. The fixture supplies a stub for every
# gate the hook invokes, each appending its own name to a `.reached` file, so what the hook did is
# observable and cheap. Two things are not stubbed: the hook itself is copied byte for byte, and
# the main-push guard is the real `scripts/git-pre-push-block-main.sh`, reached through a
# one-line wrapper that records the call and `exec`s the real file. So "deleting main is still
# refused" is proven by the guard that does the refusing, not by a stand-in for it.
#
# Nothing here touches the repository it lives in: every git invocation names the fixture.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
hook="$repo_root/scripts/hooks/pre-push"
real_guard="$repo_root/scripts/git-pre-push-block-main.sh"

fail=0
ok() { printf '  ok    %s\n' "$1"; }
bad() {
	printf '  FAIL  %s\n' "$1" >&2
	fail=1
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/er-quickload-pre-push-deletion.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

# `env -u ...` for the same reason scripts/test-git-pre-push-block-main.sh does it: this suite is
# itself run by the pre-push hook, and in a linked worktree git exports GIT_DIR to that hook. A
# `git init` that inherits it builds no fixture and edits this repository instead.
git_clean=(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE git)

remote="$tmp/remote.git"
work="$tmp/work"
"${git_clean[@]}" init -q --bare "$remote"
"${git_clean[@]}" init -q -b main "$work"
"${git_clean[@]}" -C "$work" config user.email selftest@example.invalid
"${git_clean[@]}" -C "$work" config user.name selftest
"${git_clean[@]}" -C "$work" config commit.gpgsign false
"${git_clean[@]}" -C "$work" config core.hooksPath scripts/hooks
"${git_clean[@]}" -C "$work" remote add origin "$remote"
"${git_clean[@]}" -C "$work" commit -q --allow-empty -m init
mkdir -p "$work/scripts/hooks"

# Setting up the fixture must not run the hook under test -- a hook that refuses would leave the
# remote with no branches to delete, and every case below would then be measuring git's "remote ref
# does not exist" rather than the hook's answer. `core.hooksPath` is pointed at a directory that
# does not exist, which is git's own way of running no hooks and needs no `--no-verify`.
publish() { # publish <branch>
	"${git_clean[@]}" -C "$work" -c core.hooksPath="$tmp/no-hooks" push -q origin "$1"
}

branch() { # branch <name> -- create it with one commit of its own and publish it
	"${git_clean[@]}" -C "$work" checkout -q -b "$1" main
	"${git_clean[@]}" -C "$work" commit -q --allow-empty -m "$1"
	publish "$1"
}

publish main
branch feat/a
branch feat/b
branch feat/c
branch feat/d
branch feat/e
branch feat/f

# A recording hook first, so the ref lines the cases below rely on are git's own bytes rather than
# a line this file typed. It refuses, so `feat/c` survives the attempt and is still there to be
# deleted for real further down.
cat >"$work/scripts/hooks/pre-push" <<'HOOK'
#!/usr/bin/env bash
cat >>"$(git rev-parse --show-toplevel)/.stdin-capture"
exit 1
HOOK
chmod +x "$work/scripts/hooks/pre-push"

: >"$work/.stdin-capture"
"${git_clean[@]}" -C "$work" push origin --delete feat/c >/dev/null 2>&1
captured=$(cat "$work/.stdin-capture")
if [[ "$captured" =~ ^\(delete\)[[:space:]]+0{40}[[:space:]]+refs/heads/feat/c[[:space:]] ]]; then
	ok "git writes a deletion as '(delete)' with a forty-zero local sha: $captured"
else
	bad "a real deletion did not produce the ref line this hook parses: '$captured'"
fi

# Now the real hook, over stubbed gates.
install_hook() { # install_hook <source file>
	cp -f "$1" "$work/scripts/hooks/pre-push"
	chmod +x "$work/scripts/hooks/pre-push"
}

cat >"$work/scripts/git-pre-push-block-main.sh" <<HOOK
#!/usr/bin/env bash
printf 'main-guard\n' >>.reached
exec bash "$real_guard" "\$@"
HOOK
for gate in check-runtime-evidence check-committed-compiles check; do
	cat >"$work/scripts/$gate.sh" <<HOOK
#!/usr/bin/env bash
printf '$gate %s\n' "\$*" >>.reached
exit 0
HOOK
done
cat >"$work/scripts/er-change-scope.py" <<'PY'
import sys

# Exit 0 is "cargo work is required", which keeps the compile gate on the path being measured.
open(".reached", "a").write("er-change-scope %s\n" % " ".join(sys.argv[1:]))
PY
cat >"$work/scripts/check-stages.py" <<'PY'
import sys

args = sys.argv[1:]
open(".reached", "a").write("check-stages %s\n" % " ".join(args))
# `--stages` is the whole inventory and `--stages-for-diff` a narrower selection, so the hook
# takes its narrowed branch rather than the run-everything one.
print("\n".join(["lint", "policy", "suite"] if "--stages" in args else ["lint"]))
PY
install_hook "$hook"

reached=""
status=0
output=""
push() { # push <git push arguments...> -- run one push through the real hook, record what it did
	: >"$work/.reached"
	output=$(cd "$work" && "${git_clean[@]}" push "$@" 2>&1)
	status=$?
	reached=$(cat "$work/.reached")
}

feed() { # feed <stdin> -- call the hook directly, for a stream git will not produce on demand
	: >"$work/.reached"
	output=$(cd "$work" && printf '%s' "$1" | env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
		bash scripts/hooks/pre-push origin "$remote" 2>&1)
	status=$?
	reached=$(cat "$work/.reached")
}

gates_after_the_guard() { printf '%s\n' "$reached" | grep -v '^main-guard$' | grep -c '[^[:space:]]'; }

# --- deletion-only, one ref ---------------------------------------------------------------
push origin --delete feat/a
if [[ $status -eq 0 ]]; then
	ok "deletion-only, one ref: the push is allowed"
else
	bad "deletion-only, one ref: the push was refused (status $status): $output"
fi
if [[ "$(gates_after_the_guard)" -eq 0 ]]; then
	ok "deletion-only, one ref: no gate ran"
else
	bad "deletion-only, one ref: a gate ran -- $(printf '%s' "$reached" | tr '\n' '|')"
fi
if [[ "$reached" == *main-guard* ]]; then
	ok "deletion-only, one ref: the main-push guard still ran"
else
	bad "deletion-only, one ref: the main-push guard did not run"
fi
if [[ "$output" == *"only DELETES refs"* && "$output" == *"not a pass"* ]]; then
	ok "deletion-only, one ref: the skip says on stderr that nothing ran"
else
	bad "deletion-only, one ref: the skip was silent about what it did not do: $output"
fi
if [[ "$output" == *"refs/heads/feat/a"* ]]; then
	ok "deletion-only, one ref: the skip names the ref being deleted"
else
	bad "deletion-only, one ref: the skip did not name the ref: $output"
fi

# --- deletion-only, two refs --------------------------------------------------------------
push origin --delete feat/b feat/c
if [[ $status -eq 0 && "$(gates_after_the_guard)" -eq 0 ]]; then
	ok "deletion-only, two refs: allowed, and no gate ran"
else
	bad "deletion-only, two refs: status $status, reached '$(printf '%s' "$reached" | tr '\n' '|')'"
fi
if [[ "$output" == *"refs/heads/feat/b"* && "$output" == *"refs/heads/feat/c"* ]]; then
	ok "deletion-only, two refs: the skip names both"
else
	bad "deletion-only, two refs: the skip did not name both: $output"
fi

# --- mixed: one deletion and one content ref ----------------------------------------------
"${git_clean[@]}" -C "$work" checkout -q feat/e
"${git_clean[@]}" -C "$work" commit -q --allow-empty -m "something to gate"
content_sha=$("${git_clean[@]}" -C "$work" rev-parse feat/e)
push origin ":refs/heads/feat/d" "refs/heads/feat/e:refs/heads/feat/e"
if [[ $status -eq 0 ]]; then
	ok "mixed deletion plus content: the push is allowed"
else
	bad "mixed deletion plus content: refused (status $status): $output"
fi
for gate in check-runtime-evidence check-committed-compiles check-stages check; do
	if [[ "$reached" == *"$gate "* ]]; then
		ok "mixed deletion plus content: $gate ran"
	else
		bad "mixed deletion plus content: $gate did NOT run -- $(printf '%s' "$reached" | tr '\n' '|')"
	fi
done
if [[ "$reached" == *"$content_sha"* ]]; then
	ok "mixed deletion plus content: the gates were handed the content ref's sha"
else
	bad "mixed deletion plus content: the content sha $content_sha never reached a gate"
fi

# --- empty stdin: unchanged ---------------------------------------------------------------
#
# git will not produce this on request -- a push with nothing to send exits before the hook -- so
# it is fed directly, which is also how scripts/test-git-pre-push-block-main.sh reaches the case.
# The checkout is on feat/e rather than main, so the guard's own empty-stream refusal (which is
# conditioned on a main checkout and has its own test) does not decide this.
feed ""
if [[ $status -eq 0 ]]; then
	ok "empty stdin: allowed from a non-main checkout, as before"
else
	bad "empty stdin: refused (status $status): $output"
fi
if [[ "$output" != *"only DELETES refs"* ]]; then
	ok "empty stdin: not read as a deletion"
else
	bad "empty stdin: was read as a deletion-only push"
fi
if [[ "$reached" == *"check-stages "* && "$reached" == *"check "* ]]; then
	ok "empty stdin: the gates below still run"
else
	bad "empty stdin: the gates stopped running -- $(printf '%s' "$reached" | tr '\n' '|')"
fi

# --- deleting main: still refused ----------------------------------------------------------
push origin --delete main
if [[ $status -ne 0 && "$output" == *"ER-EFFECTS-BLOCK-MAIN-PUSH"* ]]; then
	ok "deleting main: refused by the main-push guard"
else
	bad "deleting main: NOT refused (status $status): $output"
fi
if [[ "$(gates_after_the_guard)" -eq 0 ]]; then
	ok "deleting main: refused before any gate ran"
else
	bad "deleting main: a gate ran anyway -- $(printf '%s' "$reached" | tr '\n' '|')"
fi
if [[ "$output" != *"only DELETES refs"* ]]; then
	ok "deleting main: the skip never got the chance to allow it"
else
	bad "deleting main: the deletion skip ran before the guard refused"
fi

# --- negative control ----------------------------------------------------------------------
#
# Without this, every case above would pass on a hook that had never learnt about deletions:
# `git push --delete` of a branch whose gates all happen to be green looks identical to a skip
# from the outside. So the same deletion is run again through a copy of the hook with the skip
# block cut out, and it must reach the stage selector -- the exact line the 2026-09-14 push
# printed. Cut by line range rather than by pattern, so a reworded comment does not silently turn
# this control into a second copy of the case above.
skip_start=$(awk '/^if \[\[ .*pushed_refs\[@\].*pushed_shas\[@\].*\]\]; then$/ { print NR; exit }' "$hook")
if [[ -z "$skip_start" ]]; then
	bad "negative control: the deletion-only condition is not in the hook at all"
else
	skip_end=$(awk -v s="$skip_start" 'NR > s && /^fi$/ { print NR; exit }' "$hook")
	sed "${skip_start},${skip_end}d" "$hook" >"$tmp/pre-push-without-the-skip"
	install_hook "$tmp/pre-push-without-the-skip"
	push origin --delete feat/f
	if [[ "$reached" == *"check-stages --stages-for-diff"* ]]; then
		ok "negative control: without the skip, the same deletion DOES reach the stage selector"
	else
		bad "negative control did not reach the selector -- this suite proves nothing: $(printf '%s' "$reached" | tr '\n' '|')"
	fi
	install_hook "$hook"
fi

# --- position ------------------------------------------------------------------------------
#
# The condition greps fine wherever it sits. Above the main-push guard it would allow a deletion
# of main; below the runtime-evidence gate it would still let that gate judge the checked-out
# `HEAD`, which is half the defect. Position is the part that rots, so it is asserted.
# Comment lines are excluded from both searches: this file's own header names the guard, and the
# hook's prose names every gate several times before invoking any of them, so a search that reads
# comments finds line numbers that have nothing to do with what runs.
line_guard=$(awk '!/^[[:space:]]*#/ && /bash scripts\/git-pre-push-block-main\.sh/ { print NR; exit }' "$hook")
line_skip=$(awk '/^if \[\[ .*pushed_refs\[@\].*pushed_shas\[@\].*\]\]; then$/ { print NR; exit }' "$hook")
line_first_gate=$(awk '!/^[[:space:]]*#/ && /(bash scripts\/check-runtime-evidence\.sh|bash scripts\/check-committed-compiles\.sh|python3 scripts\/check-stages\.py|python3 scripts\/er-change-scope\.py)/ { print NR; exit }' "$hook")
if [[ -n "$line_guard" && -n "$line_skip" && -n "$line_first_gate" &&
	"$line_skip" -gt "$line_guard" && "$line_skip" -lt "$line_first_gate" ]]; then
	ok "the skip is at line $line_skip -- after the main guard ($line_guard), before the first gate ($line_first_gate)"
else
	bad "the skip is out of position (guard=$line_guard, skip=$line_skip, first gate=$line_first_gate)"
fi

if [[ $fail -eq 0 ]]; then
	echo "[test-pre-push-deletion-only] passed"
else
	echo "[test-pre-push-deletion-only] FAILED" >&2
fi
exit "$fail"
