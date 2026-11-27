#!/usr/bin/env bash
# Cupcake signal: session_tasks
#
# The shells and agents of this Claude Code session running now, in one paragraph: each shell's task
# id, its owner (the main session, or an agent id and its description), what it is doing and how long
# it has run, then each agent still working. Measured by scripts/cupcake_session_tasks.py --hook
# --brief from the process table and the session's transcripts, for the session the hook event on
# stdin names (its transcript_path / session_id), under the Claude Code process this hook runs under.
# Empty when nothing runs, and empty when either cannot be found: a hook that cannot tell whose
# session it is reports nothing.
#
# Read by session_tasks_context.rego on UserPromptSubmit. User words 2026-11-11, verbatim: "So next
# time, how are you going to monitor your own shells and understand when they are running so you can
# communicate clearly to the user why you're paused (blocked because X shell is running) or whatever
# you're doing". The same report is what idle_hold.rego quotes when a paused closing message names
# none of it (signal last_assistant_idle_hold).
#
# Discovered, so cupcake gives it 5 s; the helper is capped at 4 s and fails open (prints nothing).
# Measured 2026-11-11 on a session with 52 MB of transcript, 199 MB of subagent transcripts, four
# live shells and three agents: 0.65 s.
set -uo pipefail
CUPCAKE_SIGNAL_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
event=""
if [ ! -t 0 ]; then
	event="$(timeout 2 cat 2>/dev/null || true)"
fi
printf '%s' "$event" | timeout 4 python3 "$CUPCAKE_SIGNAL_REPO_ROOT/scripts/cupcake_session_tasks.py" --hook --brief 2>/dev/null || true
