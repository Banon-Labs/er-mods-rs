#!/usr/bin/env bash
# Cupcake signal: subagent_id
#
# Consumed by `.cupcake/policies/claude/no_subagent_monitor.rego`. Prints the hook event's
# `agent_id` -- set by Claude Code only on events a subagent raises -- and nothing for the main
# session. cupcake 0.5.2 passes the event to a signal's stdin with `agent_id` intact
# (scripts/cupcake_push_authorization.py), but leaves it out of the policy's `input` (measured
# 2026-11-14: a rego rule on `input.agent_id` never fired under `cupcake eval`), so the policy reads
# it here.
set -uo pipefail

# Run by hand from a terminal, stdin is a keyboard; reading it would hang instead of answering.
[ -t 0 ] && exit 0
python3 -c 'import json, sys
try:
    event = json.load(sys.stdin)
except Exception:
    sys.exit(0)
aid = event.get("agent_id") if isinstance(event, dict) else None
if isinstance(aid, str) and aid:
    print(aid)' 2>/dev/null || true
exit 0
