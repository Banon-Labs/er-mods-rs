#!/usr/bin/env bash
# Cupcake signal: last_assistant_unmeasured_estimate
#
# Emits one facts line when the most recently completed assistant turn closed on a message that
# forecasts how long or how costly work or a run will be with no measured number behind it:
#
#   UNMEASURED|kind=effort|forecast=..     development effort, or a size word where a number goes
#   UNMEASURED|kind=runtime|forecast=..    a run's duration, and no measured timing in the message
#
# A clean turn emits nothing (fail-open, like every neighbouring signal). Written from 2026-11-09:
# "it's nearly free ... a few seconds on top of a kit run ... roughly 20 to 30 minutes", with nothing
# timed, for runs that then took 6,863 to 12,716 s. The classification and its history live in
# `scripts/cupcake_unmeasured_estimate.py` (`--selftest`, `--replay`); the reasoning is in
# docs/agents/runtime-evidence.md, "Forecasts of time and effort are claims, cited or not made";
# transcript discovery and turn bucketing come from `scripts/cupcake_turn_scan.py`.
set -uo pipefail
CUPCAKE_SIGNAL_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
export CUPCAKE_SIGNAL_REPO_ROOT
# The hook event on stdin names this session's transcript; see cupcake_turn_scan.hook_transcript.
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
    import cupcake_unmeasured_estimate as est
except Exception:
    sys.exit(0)  # fail open: a missing helper must never wedge a session

path = scan.hook_transcript()
if not path:
    sys.exit(0)
turn = scan.last_text_turn(scan.split_turns(scan.load_events(path)))
if turn is None:
    sys.exit(0)

# A turn that kept going past its last prose did not end on that prose.
if turn.last_text_index < 0 or turn.tool_after(turn.last_text_index):
    sys.exit(0)
runs = turn.text_runs
if not runs:
    sys.exit(0)

found = est.unmeasured_estimate(runs[-1])
if not found:
    sys.exit(0)
kind, sentence = found
print("UNMEASURED|kind={}|forecast={}".format(kind, " ".join(sentence.replace("|", "/").split())[:240]))
PY
