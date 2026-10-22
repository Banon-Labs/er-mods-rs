#!/usr/bin/env bash
# Cupcake signal: last_assistant_unexplained_running_tasks
#
# Emits `UNEXPLAINEDTASKS:<id> (<kind>: <command>); ...` when the turn is ending while background
# tasks the agent started are still running and the closing message does not account for each one
# by its task id. Empty otherwise (fail-open).
#
# User directive 2026-10-07, in their words: "any time you have a monitor that runs after you don't
# need it, the first thing you should do is stop it". The instance: a stage-log copier loop and an
# until-loop waiter were left running in the background long after the push they served had
# finished, and were only stopped when the user complained.
#
# What counts as still running is `cupcake_turn_scan.running_background_tasks`, read from the
# transcript (the Stop hook's `transcript_path` and nothing else, via hook_transcript): a Bash or
# Monitor call whose result acknowledged a background task id ("running in background with ID:",
# "moved to the background (ID:", "backgrounded by user with ID:", "Monitor started (task ...") and
# that has had no finished `<task-notification>`, no Monitor expiry/timeout event, no finished
# `task_status` attachment and no TaskStop/KillShell naming it since.
#
# What counts as accounted for: the closing prose run of the turn names the task id, and the
# sentence (or line) that names it carries at least four other words -- the reason it is still
# needed. A bare id list ("bx1 bx2") is not a reason. Ids are matched as whole tokens anywhere in
# the closing run, backticks included, since that is how an id is normally written.
set -uo pipefail
CUPCAKE_SIGNAL_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"
export CUPCAKE_SIGNAL_REPO_ROOT
CUPCAKE_SIGNAL_EVENT=""
if [ ! -t 0 ]; then
	CUPCAKE_SIGNAL_EVENT="$(timeout 2 cat 2>/dev/null || true)"
fi
export CUPCAKE_SIGNAL_EVENT
python3 - <<'PY' 2>/dev/null || true
import json, os, re, sys

sys.path.insert(0, os.path.join(os.environ.get("CUPCAKE_SIGNAL_REPO_ROOT", "."), "scripts"))
try:
    import cupcake_turn_scan as scan
except Exception:
    sys.exit(0)

path = scan.hook_transcript()
if not path:
    sys.exit(0)

events = scan.load_events(path)
running = scan.running_background_tasks(events)
if not running:
    sys.exit(0)

turns = scan.split_turns(events)
turn = turns[-1] if turns else None
closing = turn.text_runs[-1] if turn is not None and turn.text_runs else ""

MIN_REASON_WORDS = 4
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")


def justified(task_id: str) -> bool:
    token = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(task_id) + r"(?![A-Za-z0-9_-])")
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", closing):
        if not token.search(sentence):
            continue
        rest = token.sub(" ", sentence)
        for other in running:
            rest = rest.replace(other.task_id, " ")
        if len(WORD_RE.findall(rest)) >= MIN_REASON_WORDS:
            return True
    return False


unexplained = [t for t in running if not justified(t.task_id)]
if not unexplained:
    sys.exit(0)


def describe(t) -> str:
    cmd = " ".join((t.command or "").split())
    if len(cmd) > 80:
        cmd = cmd[:77] + "..."
    return "%s (%s: %s)" % (t.task_id, t.kind, cmd)


sys.stdout.write("UNEXPLAINEDTASKS:" + "; ".join(describe(t) for t in unexplained)[:900])
PY
