#!/usr/bin/env bash
# Cupcake signal: last_assistant_unwatched_red_stage
#
# Emits `UNWATCHEDRED:<task id> (<suite>: <command>) => Monitor with timeout_ms 1800000: <monitor
# command>; ...` when the turn is ending while a background `git push` (whose pre-push hook runs
# the check suite), a hand-run pre-push hook or a full check.sh is still running and no
# still-running Monitor follows its output through a filter that passes `RED`. Empty otherwise
# (fail-open).
#
# User words 2026-11-25: "It would be wise, and prudent, to not wait until all checks pass before
# addressing any one of the failing reds in each future case
# `>>> stage policy RED (exit 1) 1702s /run/user/1000/er-mods-rs-pre-push/6abca54e80d19a080c8579b188175fa73d210e01/policy.log`". The push
# had a kitty split tailing it and nothing that woke the agent, so the red waited for the push's
# completion notification, after every other stage.
#
# The judgement is `scripts/cupcake_red_stage_watch.py` (`--selftest`), over
# `cupcake_turn_scan.running_background_tasks`, which reads the Stop hook's own transcript.
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
    import cupcake_red_stage_watch as watch
except Exception:
    sys.exit(0)  # fail open: a missing helper must never wedge a session

path = scan.hook_transcript()
if not path:
    sys.exit(0)
line = watch.signal_line(scan.load_events(path))
if line:
    sys.stdout.write(line)
PY
