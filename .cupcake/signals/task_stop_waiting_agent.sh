#!/usr/bin/env bash
# Cupcake signal: task_stop_waiting_agent
#
# Consumed by `.cupcake/policies/claude/no_stop_waiting_agent.rego`. For a TaskStop in the main
# session whose task id is a subagent still waiting on its own background work, prints one line
#
#   WAITINGAGENT|id=<agent id>|override=<0|1>|description=<description>|prompt=<user's last message>
#
# and nothing for anything else. `override=1` means the user's own last typed message names the agent
# or asks to stop it. The judgement and the incident behind it (2026-11-11: a TaskStop on agent
# abce87a3bda959d98 killed its running selftest) live in `scripts/cupcake_task_stop_waiting_agent.py`.
#
# Fails open: any other tool, a subagent's call, an unreadable transcript or a crash prints nothing,
# and the policy allows on an empty signal.
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 0
reader="$repo_root/scripts/cupcake_task_stop_waiting_agent.py"
[ -f "$reader" ] || exit 0

# Run by hand from a terminal, stdin is a keyboard; reading it would hang instead of answering.
[ -t 0 ] && exit 0
event="$(cat)"

# Only a TaskStop is judged; skip the python start for everything else.
case "$event" in
*'"tool_name":"TaskStop"'* | *'"tool_name": "TaskStop"'*) ;;
*) exit 0 ;;
esac

# Same budget as `push_authorization.sh`: cupcake drops a discovered signal after 5 s. On the
# 46 MB transcript of session 5ec7b779 the reader takes about 1.5 s.
printf '%s' "$event" | timeout 4.5 python3 "$reader" 2>/dev/null || true
exit 0
