#!/usr/bin/env bash
# Cupcake signal: last_assistant_markdown_rule
#
# Emits one facts line when the most recently completed assistant turn closed on a message that calls
# text in a markdown file a rule ("The rule is now in docs/agents/runtime-evidence.md", "AGENTS.md
# now carries the same rule"):
#
#   MDRULE|referent=..|sentence=..
#
# A clean turn emits nothing (fail-open, like every neighbouring signal). User correction 2026-11-11:
# "This is probably the 100th time I've told you that its logically inconsistent to use the word rule
# in reference to markdown files". The classification and its history live in
# `scripts/cupcake_markdown_rule.py` (`--selftest`, `--replay`); transcript discovery and turn
# bucketing come from `scripts/cupcake_turn_scan.py`, as for the other Stop signals.
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
    import cupcake_markdown_rule as mdrule
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

found = mdrule.markdown_rule(runs[-1])
if not found:
    sys.exit(0)
sentence, referent = found


def field(s, n):
    return " ".join(s.replace("|", "/").split())[:n]


print("MDRULE|referent={}|sentence={}".format(field(referent, 120), field(sentence, 240)))
PY
