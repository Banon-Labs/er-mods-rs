#!/usr/bin/env bash
# Is an Elden Ring run live right now, and would the pending edit tear it down?
#
# Read by `.cupcake/policies/claude/no_source_edit_during_live_run.rego`, which refuses an edit that
# would tear that run down. The teardown is not hypothetical: the PostToolUse hook runs
# `scripts/er-stale-run-sentinel.sh`, which kills a live run the moment an edited file feeds a DLL
# that run loaded. That is the correct invariant -- the run's loaded code no longer matches the tree
# -- but it fires after the edit, so the first anyone knows of it is the game closing under them.
#
# Prints, when a run is up:
#
#   `[er-sentinel] LIVE: ...`           the sentinel's own `status` block
#   `VERDICT <verdict>\t<branch>\t...`  the sentinel's `verdict` for the pending edit's path
#
# and nothing otherwise. The verdict is the very function `check` acts on after the edit, so the
# policy and the teardown decide on one implementation and cannot drift. On 2026-10-02 they did:
# the policy matched `crates/` only, while the sentinel tears down for a `docs/recon/*.tsv` that
# `er-game-base/build.rs` compiles into every product DLL.
#
# Never fails and never kills: a signal that errors would take the policy with it, and the fail-open
# direction is "no live run", which leaves editing exactly as permissive as it was before this file.
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)" || exit 0
sentinel="$repo_root/scripts/er-stale-run-sentinel.sh"
[ -f "$sentinel" ] || exit 0

# Cupcake pipes the pending event to every signal on stdin. Guarded on a pipe: run by hand from a
# terminal, stdin is a keyboard and reading it would hang.
event=""
if [ ! -t 0 ]; then
	event="$(cat 2>/dev/null || true)"
fi

# LIVE_ER_RUN_PROFILES_OVERRIDE stands in for the live run, for scripts/test-live-er-run-signal.py:
# set and empty means nothing is live, set to a `:`-separated list of `.me3` paths means those
# profiles are the run. It mirrors CUPCAKE_CURRENT_BRANCH_OVERRIDE in the rulebook. Cupcake hands
# signals its own environment, so an agent's shell cannot set it for the real hook.
profiles=()
if [ -n "${LIVE_ER_RUN_PROFILES_OVERRIDE+set}" ]; then
	[ -n "$LIVE_ER_RUN_PROFILES_OVERRIDE" ] || exit 0
	IFS=: read -r -a profiles <<<"$LIVE_ER_RUN_PROFILES_OVERRIDE"
	printf "[er-sentinel] LIVE: (override)\n"
	printf "[er-sentinel]   %s\n" "${profiles[@]}"
else
	# `status` is the sentinel's own read-only mode: it prints "[er-sentinel] LIVE:" and exits 1
	# when a run is up, "[er-sentinel] no live run" and exits 0 otherwise. Bounded, because this
	# runs in front of every Write/Edit and a hang here would stall the session rather than
	# protect it.
	out="$(timeout 5 bash "$sentinel" status 2>/dev/null)" || true
	case "$out" in
		*LIVE*) : ;;
		*) exit 0 ;;
	esac
	printf "%s\n" "$out"
fi

# The edited path, from the same keys the sentinel's PostToolUse `hook` mode reads.
path=""
if [ -n "$event" ]; then
	path="$(printf '%s' "$event" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = d.get("tool_input") or {}
for key in ("file_path", "notebook_path", "path"):
    v = ti.get(key)
    if isinstance(v, str) and v:
        print(v)
        break
' 2>/dev/null || true)"
fi

# Relative paths resolve against the repo, which is the directory the agent's tools run in.
if [ -n "$path" ]; then
	case "$path" in
		/*) : ;;
		*) path="$repo_root/$path" ;;
	esac
	verdict="$(timeout 6 bash "$sentinel" verdict "$path" "${profiles[@]}" 2>/dev/null | head -n 1)" || true
	if [ -n "$verdict" ]; then
		printf "VERDICT %s\n" "$verdict"
		exit 0
	fi
fi

# No verdict: no path in the event, or the classifier timed out. Fall back to the crate directories
# the run's DLLs compile in, one `CLOSURE crates/<name>` line each, which the policy uses to refuse
# `crates/` edits it can still attribute. Printing none of them makes it refuse every `crates/` edit
# while live -- the classifier could not answer, which is not the same as "nothing is at stake".
timeout 6 bash "$sentinel" closure 2>/dev/null | while IFS= read -r crate; do
	[ -n "$crate" ] && printf "CLOSURE %s\n" "$crate"
done
exit 0
