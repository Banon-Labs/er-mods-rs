#!/usr/bin/env bash
# Cupcake signal: last_assistant_filed_not_started
#
# Emits one facts line when the most recently completed assistant turn filed work (`bd create`) and
# ended without starting it -- no Agent, Workflow, Edit or Write after the last `bd create` -- and
# its closing message names no blocker:
#
#   FILEDFACTS|kind=filed|filed=<count>|closing=<first sentence>
#
# or, when the closing message parks a written fix behind a running job ("I'll apply it when the
# kit run (b3fgttzcf) ends", added 2026-11-20):
#
#   FILEDFACTS|kind=deferred|closing=<the clause that defers it>
#
# A clean turn emits nothing (fail-open, like every neighbouring signal). Written from 2026-11-10:
# a mechanic the model lacked was filed as a beads issue and the turn closed on "It waits
# for the homing and requirement-line changes", which blocked nothing. The classification and its
# history live in `scripts/cupcake_filed_not_started.py` (`--selftest`); transcript discovery and
# turn bucketing come from `scripts/cupcake_turn_scan.py`, as for the other Stop signals.
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
import os, re, sys

sys.path.insert(0, os.path.join(os.environ.get("CUPCAKE_SIGNAL_REPO_ROOT", "."), "scripts"))
try:
    import cupcake_turn_scan as scan
    import cupcake_filed_not_started as fns
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
closing = runs[-1]

filed = fns.filed_not_started(turn.tools(), closing)
if filed:
    first = re.split(r"(?<=[.!?])\s+", " ".join(closing.split()), maxsplit=1)[0]
    print("FILEDFACTS|kind=filed|filed={}|closing={}".format(filed, first.replace("|", "/")[:200]))
    sys.exit(0)
deferred = fns.deferred_apply(closing)
if deferred:
    print("FILEDFACTS|kind=deferred|closing={}".format(deferred.replace("|", "/")[:200]))
PY
