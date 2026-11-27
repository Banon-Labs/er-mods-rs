#!/usr/bin/env bash
# Cupcake signal: user_prompt_correction
#
# On UserPromptSubmit, emits `CORRECTION|<sentence>` when the prompt being submitted reads as a
# correction of how the agent works, and nothing otherwise. The prompt is the hook event's own
# `prompt` field, read from stdin: at UserPromptSubmit time the message is not in the transcript yet.
#
# Read by correction_encoded_first_reminder.rego, which tells the turn that its first change must
# encode the correction for future agents (user directive 2026-11-11: "My largest concern is that
# every time I correct you, you fix the issue instead of fix it for future agents first"). The
# classifier is `scripts/cupcake_correction_first.correction`, the same one the Stop guard
# correction_encoded_first.rego judges the turn by, so the two cannot disagree about whether a
# prompt was a correction.
set -uo pipefail
CUPCAKE_SIGNAL_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
export CUPCAKE_SIGNAL_REPO_ROOT
CUPCAKE_SIGNAL_EVENT=""
if [ ! -t 0 ]; then
	CUPCAKE_SIGNAL_EVENT="$(timeout 2 cat 2>/dev/null || true)"
fi
export CUPCAKE_SIGNAL_EVENT
python3 - <<'PY' 2>/dev/null || true
import json, os, sys

sys.path.insert(0, os.path.join(os.environ.get("CUPCAKE_SIGNAL_REPO_ROOT", "."), "scripts"))
try:
    import cupcake_correction_first as cf
except Exception:
    sys.exit(0)  # fail open: a missing helper must never wedge a session

try:
    event = json.loads(os.environ.get("CUPCAKE_SIGNAL_EVENT") or "{}")
except ValueError:
    sys.exit(0)
prompt = event.get("prompt") if isinstance(event, dict) else None
if not isinstance(prompt, str):
    sys.exit(0)
said = cf.correction(prompt)
if said:
    print("CORRECTION|" + said.replace("|", "/"))
PY
