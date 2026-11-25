#!/usr/bin/env bash
# Cupcake signal: last_assistant_correction_encoded_first
#
# Emits one facts line when the user's latest typed prompt reads as a correction and the turn it
# opened did not encode that correction for future agents before acting on the instance:
#
#   CORRECTIONFACTS|correction=<sentence>|verdict=instance_first|unencoded|first_instance=<tool>|encoded_later=0|1
#
# A clean turn emits nothing (fail-open, like every neighbouring signal).
#
# User directive 2026-11-11, verbatim: "My largest concern is that every time I correct you, you fix
# the issue instead of fix it for future agents first". Measured in session 5ec7b779: after the
# correction "If I ever see a subagent that is finished but hasn't been closed ...", the turn ran
# ListAgents, two read-only checks, a TaskStop and a SendMessage and closed on prose; the Stop guard
# came a turn later, after a second correction.
#
# The turn is everything after the latest prompt the user typed (cupcake_push_authorization.prompt_text),
# so a task notification or this guard's own Stop feedback does not start a new one. The classifier,
# the enforcement/instance split and the halt rule live in `scripts/cupcake_correction_first.py`
# (`--selftest`), shared with the UserPromptSubmit signal `user_prompt_correction.sh`.
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
    import cupcake_correction_first as cf
except Exception:
    sys.exit(0)  # fail open: a missing helper must never wedge a session

path = scan.hook_transcript()
if not path:
    sys.exit(0)
line = cf.judge_transcript(path)
if line:
    print(line)
PY
