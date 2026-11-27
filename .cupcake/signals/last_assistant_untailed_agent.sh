#!/usr/bin/env bash
# Cupcake signal: last_assistant_untailed_agent
#
# Emits `UNTAILEDAGENT:<agent id> ("<description>"); ... => python3 scripts/kitty-tail-agent.py <id> ...`
# when the turn being stopped started a background subagent that is still working and opened no
# kitty tab following its transcript. Empty otherwise (fail-open).
#
# User, 2026-11-11: "I don't understand how you could have a round running without me being able to
# see the output". The main session had launched a background Agent ("Speed up attack sets, round
# 2") with no view the user could open.
#
# What counts is `scripts/cupcake_untailed_agent.py` (`untailed_agents`, documented there; `--replay`
# judges recent transcripts): an Agent call answered with a background launch naming its agentId, or
# a SendMessage that resumed one, with no later `scripts/kitty-tail-agent.py` call naming that id,
# while the agent has not finished. Transcript discovery is `cupcake_turn_scan.hook_transcript`.
set -uo pipefail
CUPCAKE_SIGNAL_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
export CUPCAKE_SIGNAL_REPO_ROOT
CUPCAKE_SIGNAL_EVENT=""
if [ ! -t 0 ]; then
	CUPCAKE_SIGNAL_EVENT="$(timeout 2 cat 2>/dev/null || true)"
fi
export CUPCAKE_SIGNAL_EVENT
python3 - <<'PY' 2>/dev/null || true
import os, sys

sys.path.insert(0, os.path.join(os.environ.get("CUPCAKE_SIGNAL_REPO_ROOT", "."), "scripts"))
try:
    import cupcake_turn_scan as scan
    import cupcake_untailed_agent as ua
except Exception:
    sys.exit(0)  # fail open: a missing helper must never wedge a session

path = scan.hook_transcript()
if not path:
    sys.exit(0)
line = ua.signal_line(scan.load_events(path))
if line:
    sys.stdout.write(line)
PY
