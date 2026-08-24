#!/usr/bin/env bash
# Run a cupcake hook evaluation, surviving harness permission modes cupcake does not know yet.
#
# TWO BUGS THIS EXISTS TO FIX, both observed live on 2026-08-24 with cupcake 0.5.2:
#
# 1. NEW PERMISSION MODES ARE FATAL. Claude Code grew an `auto` permission mode; cupcake
#    deserializes `permission_mode` into a closed enum and exits 1 on anything outside
#    {default, plan, acceptEdits, bypassPermissions}:
#
#        Error: unknown variant `auto`, expected one of `default`, `plan`, ...
#
#    That is not a Stop-hook problem, it is EVERY hook -- PreToolUse and PostToolUse included --
#    so every guard in .cupcake/policies went inert for a whole session while the suite stayed
#    green. This repo has been here before (see the check.sh comment about every guard being
#    partly or wholly inert until 2026-08-22), which is exactly why the mode is normalised here
#    rather than waited on: an unrecognised mode must degrade to "evaluate anyway", never to
#    "evaluate nothing".
#
# 2. THE DEFAULT LOG LEVEL IS `info`. Unset, cupcake writes ~60 INFO lines to stderr on every
#    single hook -- policy-by-policy parse chatter, WASM compilation, signal gathering. The
#    user's own global config already passes `--log-level error`; the repo's hooks did not.
#
# stdout, stdin and the exit code are passed through untouched: the exit code is how cupcake
# denies an action, so swallowing it would disable the guards just as thoroughly as bug 1.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
CUPCAKE_BIN="${CUPCAKE_BIN:-cupcake}"

# Modes cupcake 0.5.2 accepts. Anything else is rewritten to `default`, which is the least
# privileged of them -- a mode we cannot interpret must never be treated as more permissive than
# the one the user is actually in.
normalized=$(python3 -c '
import json, sys

KNOWN = {"default", "plan", "acceptEdits", "bypassPermissions"}
raw = sys.stdin.read()
try:
    payload = json.loads(raw)
except (ValueError, TypeError):
    # Not JSON we understand: hand it over untouched and let cupcake be the one to complain.
    sys.stdout.write(raw)
    sys.exit(0)
if isinstance(payload, dict) and payload.get("permission_mode") not in KNOWN:
    if "permission_mode" in payload:
        payload["permission_mode"] = "default"
sys.stdout.write(json.dumps(payload))
')

printf '%s' "$normalized" | "$CUPCAKE_BIN" eval \
	--harness claude \
	--log-level error \
	--policy-dir "$repo_root/.cupcake" \
	--global-config "$repo_root/.cupcake/rulebook.yml"
exit "${PIPESTATUS[1]}"
