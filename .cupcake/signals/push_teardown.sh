#!/usr/bin/env bash
# Cupcake signal: push_teardown
#
# Consumed by `.cupcake/policies/claude/teardown_must_relaunch.rego`, which admits a standalone
# `er-teardown.py --reason=push-<token>` only when this prints a line starting `AUTHORIZED`: the
# user's last message is exactly `authorize <sha>` for a pushable branch tip and has not yet paid
# for a push. User directive 2026-10-22: "to authorize a push sha is to authorize a teardown". The
# judgement and its reasoning live in `scripts/cupcake_push_teardown.py`.
#
# Prints nothing for a command that does not name both the teardown script and the push reason,
# which is nearly every Bash call. Fails closed: printing nothing means the policy denies the push
# form. There is deliberately no override variable.
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 0
reader="$repo_root/scripts/cupcake_push_teardown.py"
[ -f "$reader" ] || exit 0

# Run by hand from a terminal, stdin is a keyboard; reading it would hang instead of answering.
[ -t 0 ] && exit 0
event="$(cat)"

case "$event" in
*er-teardown.py*--reason=push-*) ;;
*) exit 0 ;;
esac

# The rulebook gives this signal 8 s; the reader's transcript wait is capped at 3 s.
printf '%s' "$event" | timeout 7 python3 "$reader" 2>/dev/null || true
exit 0
