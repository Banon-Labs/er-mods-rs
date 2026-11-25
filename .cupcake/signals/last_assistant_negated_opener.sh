#!/usr/bin/env bash
# Cupcake signal: last_assistant_negated_opener
#
# Emits one facts line when the most recently completed assistant turn answered the user's
# what/how/why/which question by opening its closing message with Nothing or None, and the question
# was not about change or absence:
#
#   NEGOPENER|question=..|opener=..
#
# A clean turn emits nothing (fail-open, like every neighbouring signal). The turn it was written
# from (2026-11-10): "What does this mean to me?" answered by "Nothing in the game changed." -- a
# non-event nobody had asked about, ahead of the answer. The classification and its history live in
# `scripts/cupcake_negated_opener.py` (`--selftest`); transcript discovery and turn bucketing come
# from `scripts/cupcake_turn_scan.py`, as for the other Stop signals.
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
    import cupcake_negated_opener as neg
except Exception:
    sys.exit(0)  # fail open: a missing helper must never wedge a session

path = scan.hook_transcript()
if not path:
    sys.exit(0)
events = scan.load_events(path)
turns = scan.split_turns(events)
turn = scan.last_text_turn(turns)
if turn is None:
    sys.exit(0)

# A turn that kept going past its last prose did not end on that prose.
if turn.last_text_index < 0 or turn.tool_after(turn.last_text_index):
    sys.exit(0)

runs = turn.text_runs
if not runs:
    sys.exit(0)
closing = runs[-1]


# `split_turns` keeps blocks, not the prompt that opened the turn, so the prompt is recovered the
# way the neighbouring signals recover it: the last real user prompt in the transcript.
def prompt_text(ev):
    content = ev.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


prompt = ""
try:
    for ev in events:
        if scan.is_real_user_prompt(ev):
            prompt = prompt_text(ev)
except Exception:
    prompt = ""

found = neg.negated_opener(prompt, closing)
if not found:
    sys.exit(0)


def field(s):
    return " ".join(s.replace("|", "/").split())[:200]


print("NEGOPENER|question={}|opener={}".format(field(found[0]), field(found[1])))
PY
