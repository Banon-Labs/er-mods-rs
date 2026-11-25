#!/usr/bin/env bash
# Cupcake signal: last_assistant_ci_running_claim
#
# Emits `UNWATCHEDCI:<claim sentence> => <what to start>` when the closing message of the turn being
# stopped says GitHub CI is still running, pending, queued or unfinished, and no background task of
# this session that is still running follows that pull request's checks. Empty otherwise
# (fail-open).
#
# The instance, 2026-11-25: after a push, the main session closed with "CI on GitHub is still
# running; `compiles` and `cargo-test` hadn't finished at last check." The state came from the
# UserPromptSubmit `CI STATE (measured, not remembered)` line, read once; nothing in the session was
# watching the checks and no kitty split tailed a watcher. The user: "\" CI on GitHub is still
# running\" I don't believe you because your rego policy that told you to open a kitty tab/split to
# tail the output didn't run". Remote CI is not a local task, so the background-task guards had
# nothing to fire on.
#
# The claim is `cupcake_ci_running_claim.ci_running_claims` over the closing prose run (quotes
# folded away, so quoting the injection or the user is not a claim; a report that CI finished has
# no running predicate). A watcher is a task from `cupcake_turn_scan.running_background_tasks` --
# a Bash or Monitor task with no finished notification, expiry or TaskStop since its launch --
# whose command `cupcake_ci_running_claim.follows`: `gh pr checks [<pr>] --watch`, `gh run watch`,
# or an until/while loop polling `gh pr checks` / `gh run view`, for the PR the claim names (a
# watcher naming no PR, or a claim naming none, matches any). The PR number in the suggestion is
# the one the claim names, else the latest `CI STATE` injection's.
#
# Tailing that watcher in a kitty split is `no_untailed_long_run`'s job: its LONG_RUNS list holds
# `gh pr checks --watch` and `gh run watch`.
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
    import cupcake_ci_running_claim as ci
except Exception:
    sys.exit(0)  # fail open: a missing helper must never wedge a session

path = scan.hook_transcript()
if not path:
    sys.exit(0)
events = scan.load_events(path)
turn = scan.last_text_turn(scan.split_turns(events))
if turn is None or not turn.text_runs:
    sys.exit(0)
closing = turn.text_runs[-1]

claims = ci.ci_running_claims(closing)
if not claims:
    sys.exit(0)

prs = ci.claimed_prs(" ".join(claims)) or ci.claimed_prs(closing)
if any(ci.follows(t.command, prs) for t in scan.running_background_tasks(events)):
    sys.exit(0)

INJECTED_PR_RE = re.compile(r"CI STATE \(measured[^)]*\): PR #(\d+)")
pr = sorted(prs, key=int)[0] if prs else None
if pr is None:
    for ev in events:
        att = ev.get("attachment")
        if not isinstance(att, dict) or att.get("type") != "hook_additional_context":
            continue
        content = att.get("content")
        text = "\n".join(content) if isinstance(content, list) else str(content or "")
        for m in INJECTED_PR_RE.finditer(text):
            pr = m.group(1)

sys.stdout.write(("UNWATCHEDCI:\"%s\" => %s" % (ci.readable(claims[0]), ci.suggestion(pr)))[:1200])
PY
