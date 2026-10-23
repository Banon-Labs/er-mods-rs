#!/usr/bin/env bash
# Cupcake signal: stamped_run
#
# Consumed by `.cupcake/policies/claude/teardown_must_relaunch.rego`, which admits a standalone
# `er-teardown.py --reason=stamped-<token>` only when this prints a line starting `PROVEN`: the
# newest `br-*` run is up, it has written its `build git=` stamp on the branch it was launched
# from, and `scripts/check-runtime-evidence.sh` passes for that branch's tip in its checkout. That
# is the run a push is waiting on, and `scripts/check.sh` (the pre-push hook) refuses while it is
# up. The judgement and its reasoning live in `scripts/cupcake_stamped_run.py`.
#
# Prints nothing for a command that does not name both the teardown script and the stamped reason,
# which is nearly every Bash call: the policy reads this signal for every one, and the reader only
# has work to do for the one spelling it can admit.
#
# Fails closed, like `frida_evidence.sh` and unlike `live_er_run.sh`: printing nothing means the
# policy sees no proof and denies the stamped form. There is deliberately no override variable.
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 0
reader="$repo_root/scripts/cupcake_stamped_run.py"
[ -f "$reader" ] || exit 0

# Run by hand from a terminal, stdin is a keyboard; reading it would hang instead of answering.
[ -t 0 ] && exit 0
event="$(cat)"

case "$event" in
*er-teardown.py*--reason=stamped-*) ;;
*) exit 0 ;;
esac

# The rulebook gives this signal 30 s; the reader caps its evidence run at 22 s, so a slow answer
# is an `UNPROVEN` from here rather than a dropped signal.
printf '%s' "$event" | timeout 28 python3 "$reader" 2>/dev/null || true
exit 0
