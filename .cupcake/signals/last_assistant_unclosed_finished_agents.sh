#!/usr/bin/env bash
# Cupcake signal: last_assistant_unclosed_finished_agents
#
# Emits `UNCLOSEDAGENTS:<agent id> ("<description>"); ...` when the turn is ending while a subagent the
# main session launched has finished and the main session has not closed it with TaskStop. Empty
# otherwise (fail-open).
#
# User directive 2026-11-11, verbatim, one quoted line at a time:
#   "If I ever see a subagent that is finished but hasn't been closed, I always have VERY HIGH"
#   "concerns that you consumed its results. I'm VERY WORRIED that you missed the changes UNTIL"
#   "you close it"
#
# What counts is `cupcake_turn_scan.finished_open_agents`, read from the transcript (the Stop hook's
# `transcript_path` and nothing else, via hook_transcript): an agent whose launch result in this
# transcript named its agentId, whose latest notification says `Agent "<description>" finished`, and
# that no TaskStop has named since. "Is waiting on its own background work" is not finished, and a
# SendMessage answered "Resuming agent" makes it running again until it notifies. Agents a subagent
# launched are not counted: their launches are not in this transcript.
#
# Unlike the running-tasks guard, nothing in the closing message excuses an open agent. The close is
# the act the user watches for, so the only way past this signal is the TaskStop.
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
except Exception:
    sys.exit(0)

path = scan.hook_transcript()
if not path:
    sys.exit(0)

open_agents = scan.finished_open_agents(scan.load_events(path))
if not open_agents:
    sys.exit(0)

# Every id is named, since TaskStop needs each one; descriptions go in while the line stays short.
BUDGET = 1400
parts, used = [], 0
for agent in open_agents:
    desc = " ".join((agent.description or "").split())
    if len(desc) > 60:
        desc = desc[:57] + "..."
    entry = '%s ("%s")' % (agent.agent_id, desc) if desc else agent.agent_id
    if used + len(entry) > BUDGET:
        entry = agent.agent_id
    parts.append(entry)
    used += len(entry) + 2
sys.stdout.write("UNCLOSEDAGENTS:" + "; ".join(parts))
PY
