# shellcheck shell=bash
# Turn a cupcake run that produced no verdict into a hook reply the agent can read.
#
# Claude Code treats a hook that exits with anything but 0 or 2 as a non-blocking error: it shows
# the stderr in the user's terminal and delivers nothing to the model. So when `cupcake eval`
# cannot start, every guard is silent and the agent is never told. Measured 2026-10-16: with /tmp
# full the engine could not extract its WASM bundle, exited 1 with an empty stdout, and the only
# trace was a "Stop hook error" line the user had to paste back into the conversation. The engine
# also ends every startup failure with a hint to install the OPA CLI, which was not the cause --
# the same hint follows a missing TMPDIR.
#
# A reply at exit 0 does reach the model, through the field each event reads:
#
#   PreToolUse                            a deny whose reason names the cause; nothing checked
#                                         the call, so it is refused, the same stance the WASM
#                                         out-of-memory case already takes
#   UserPromptSubmit, PostToolUse,        `additionalContext`; the prompt or edit stands, and the
#   SessionStart                          agent learns no guard looked at it
#   Stop, SubagentStop                    a block, once: the reason reaches the agent, and the
#                                         re-entry (`stop_hook_active`) is let through so a
#                                         failure that persists cannot hold the turn open
#   anything else                         `systemMessage`, for the user only
#
# Every reply also carries `systemMessage`, so the user sees it as well. The JSON is built with
# printf from text that `cupcake_json_safe` has stripped of quotes, backslashes and control
# characters: a second interpreter here would be one more thing that can fail in the path whose
# job is to report failure.
#
# Sourced by scripts/cupcake-hook.sh. ~/.claude/hooks/cupcake-hook.sh sources a verbatim copy at
# ~/.claude/hooks/lib/cupcake-non-verdict.sh, so the two wrappers give the same reply.

# stdin to one line with no quotes, backslashes, ANSI colour or control characters, cut to $1.
cupcake_json_safe() {
	# \042 is the double quote and \134 the backslash.
	sed 's/\x1b\[[0-9;]*m//g' | tr '\n\t' '  ' | tr -d '\000-\037\042\134' | cut -c1-"${1:-600}"
}

# A file for the engine's stderr, kept off /tmp when possible: a full /tmp is one of the failures
# this reports, and the cause cannot be read back from a file that could not be written.
cupcake_scratch_file() {
	local dir
	for dir in "${XDG_RUNTIME_DIR:-}" "${TMPDIR:-}" /tmp /dev/shm; do
		if [ -n "$dir" ] && [ -d "$dir" ] && [ -w "$dir" ]; then
			mktemp "$dir/cupcake-hook-stderr.XXXXXX" 2>/dev/null && return 0
		fi
	done
	return 1
}

# The line of the engine's stderr that names the cause, without its log prefix. The engine logs
# `<time> ERROR Fatal: <cause>` and then `Error: Could not start the Cupcake engine.`, so the
# first `Fatal:` wins over the first `Error:`, and the first non-blank line is the fallback.
cupcake_failure_cause() {
	local file=$1 text=""
	if [ -n "$file" ] && [ -s "$file" ]; then
		text=$(sed 's/\x1b\[[0-9;]*m//g' "$file" | grep -m1 'Fatal:')
		[ -n "$text" ] || text=$(sed 's/\x1b\[[0-9;]*m//g' "$file" | grep -m1 'Error:')
		[ -n "$text" ] || text=$(grep -m1 -v '^[[:space:]]*$' "$file")
		text=$(printf '%s' "$text" | sed -E 's/^.*(Fatal|Error): //')
	fi
	printf '%s' "${text:-the engine wrote no error text}" | cupcake_json_safe 500
}

# The notice for an engine that exited $1 with the cause $2.
cupcake_engine_failure_notice() {
	printf 'cupcake could not evaluate this event (exit %s): %s. No guard ran. If the engine also printed a hint to install the OPA CLI, ignore it: it prints that after every startup failure.' \
		"$1" "$2"
}

# Print the reply for the event in $1 (the raw hook payload) carrying the notice in $2, which
# must already be JSON-safe.
cupcake_non_verdict() {
	local event=$1 notice=$2 name=""
	if [[ $event =~ \"hook_event_name\"[[:space:]]*:[[:space:]]*\"([A-Za-z]+)\" ]]; then
		name=${BASH_REMATCH[1]}
	fi
	case $name in
	PreToolUse)
		notice="$notice This tool call is refused, because nothing checked it."
		printf '{"systemMessage": "cupcake-hook: %s", "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "%s"}}' \
			"$notice" "$notice"
		;;
	UserPromptSubmit | PostToolUse | SessionStart)
		printf '{"systemMessage": "cupcake-hook: %s", "hookSpecificOutput": {"hookEventName": "%s", "additionalContext": "cupcake-hook: %s"}}' \
			"$notice" "$name" "$notice"
		;;
	Stop | SubagentStop)
		if [[ $event =~ \"stop_hook_active\"[[:space:]]*:[[:space:]]*true ]]; then
			printf '{"systemMessage": "cupcake-hook: %s"}' "$notice"
		else
			notice="$notice No Stop guard checked this turn's closing message."
			printf '{"systemMessage": "cupcake-hook: %s", "decision": "block", "reason": "cupcake-hook: %s"}' \
				"$notice" "$notice"
		fi
		;;
	*)
		printf '{"systemMessage": "cupcake-hook: %s"}' "$notice"
		;;
	esac
}
