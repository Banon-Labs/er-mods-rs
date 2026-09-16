#!/usr/bin/env bash
# Is an Elden Ring run live right now?
#
# Read by `.cupcake/policies/claude/no_source_edit_during_live_run.rego`, which refuses a source edit
# that would tear that run down. The teardown is not hypothetical: the PostToolUse hook runs
# `scripts/er-stale-run-sentinel.sh`, which kills a live run the moment an edited file feeds a DLL
# that run loaded. That is the correct invariant -- the run's loaded code no longer matches the tree
# -- but it fires after the edit, so the first anyone knows of it is the game closing under them.
#
# Prints `LIVE` plus the profile lines when a run is up, and nothing otherwise.
#
# Never fails and never kills: a signal that errors would take the policy with it, and the fail-open
# direction is "no live run", which leaves editing exactly as permissive as it was before this file.
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)" || exit 0
sentinel="$repo_root/scripts/er-stale-run-sentinel.sh"
[ -f "$sentinel" ] || exit 0

# `status` is the sentinel's own read-only mode: it prints "[er-sentinel] LIVE:" and exits 1 when a
# run is up, "[er-sentinel] no live run" and exits 0 otherwise. Bounded, because this runs in front
# of every Write/Edit and a hang here would stall the session rather than protect it.
out="$(timeout 5 bash "$sentinel" status 2>/dev/null)" || true
case "$out" in
	*LIVE*) : ;;
	*) exit 0 ;;
esac

printf "%s\n" "$out"

# The crate directories whose source compiles into a DLL this run actually loaded, one per line,
# each prefixed so the policy can match a path against them without mistaking a status line for one.
#
# Why the policy needs them: it used to deny every `crates/` edit while anything was live, and its
# own comment admitted the gap ("the sentinel's own classifier is finer than this ... but a policy
# cannot shell out per path"). A policy cannot, but a signal can, and the over-broad deny refused
# edits to crates the classifier itself reports as skipped -- measured 2026-09-16 on
# `crates/er-invasion-warp/src/local_invasion_filter.rs` against a run launched `--without
# er-invasion-warp`, where `classify` said `SKIP crate-builds-no-loaded-dll` and the PreToolUse
# policy denied the edit anyway.
#
# Bounded like the status call above, and fail-open in the same direction: printing no closure lines
# leaves the policy with nothing to match, so the edit is allowed. That is the correct direction
# because the PostToolUse sentinel still runs and still refuses to let a stale run continue.
timeout 20 bash "$sentinel" closure 2>/dev/null | while IFS= read -r crate; do
	[ -n "$crate" ] && printf "CLOSURE %s\n" "$crate"
done
exit 0
