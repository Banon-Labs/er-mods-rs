#!/usr/bin/env bash
# Cupcake signal: push_authorization
#
# Consumed by `.cupcake/policies/claude/git_block_any_push.rego`. Prints one tab-separated line for
# a command that mentions a push:
#
#   `AUTHORIZED <branch> <tip sha> <message uuid>`   the user's last message is exactly
#                                                  `authorize <sha>`, the sha is this branch's tip,
#                                                  and that message has not paid for another push
#   `DENIED <branch|-> <tip|-> <reason>`             anything else
#
# and nothing for a command that does not. The judgement and its reasoning live in
# `scripts/cupcake_push_authorization.py`; read its docstring for what counts as the user's message.
#
# Fails closed: the policy allows a push only on an `AUTHORIZED` line, so a missing reader, a crash
# or a timeout leaves every push denied. There is deliberately no override variable: anything that
# could make this print `AUTHORIZED` without the user typing it is the hole the design exists to
# close. Tests feed the reader a fixture transcript and a throwaway repository instead.
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 0
reader="$repo_root/scripts/cupcake_push_authorization.py"
[ -f "$reader" ] || exit 0

# Run by hand from a terminal, stdin is a keyboard; reading it would hang instead of answering.
[ -t 0 ] && exit 0
event="$(cat)"

# Nearly every Bash call names no push; skip the python start for those.
case "$event" in
*push*) ;;
*) exit 0 ;;
esac

# cupcake drops a discovered signal that has not answered in 5 s (measured 2026-10-14, cupcake
# 0.5.2) and does not kill it. 4.5 s ends the reader before that, so a reader that is still waiting
# for the transcript when cupcake gives up cannot record a spend for a push cupcake has already
# denied. The reader's own wait stops at 1 s, after which it reads the chain from its head;
# `LOCATE_DEADLINE_SECONDS` in the reader has the arithmetic.
printf '%s' "$event" | timeout 4.5 python3 "$reader" 2>/dev/null || true
exit 0
