#!/usr/bin/env bash
# Cupcake signal: tool_miss_report
#
# Consumed by `.cupcake/policies/claude/no_product_work_before_tool_fix.rego`. For an Agent/Task
# call in the main session, prints one line
#
#   TOOLMISS|reported=<0|1>|prompt=<first 160 chars of the user's last typed message>
#
# where `reported=1` means that message reports a guard or tool missing something ("Multiple rego
# violations uncaught in this response", "your tools aren't working"). The judgement lives in
# `scripts/cupcake_tool_miss_report.py`, which also carries the directive and the incident.
#
# Fails open: any other tool, a subagent, an unreadable transcript or a crash prints nothing, and
# the policy allows on an empty signal.
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 0
reader="$repo_root/scripts/cupcake_tool_miss_report.py"
[ -f "$reader" ] || exit 0

# Run by hand from a terminal, stdin is a keyboard; reading it would hang instead of answering.
[ -t 0 ] && exit 0
event="$(cat)"

# Most calls are not a dispatch; skip the python start for those.
case "$event" in
*'"tool_name":"Agent"'* | *'"tool_name": "Agent"'* | *'"tool_name":"Task"'* | *'"tool_name": "Task"'*) ;;
*) exit 0 ;;
esac

# Same budget as `push_authorization.sh`: cupcake drops a discovered signal after 5 s.
printf '%s' "$event" | timeout 4.5 python3 "$reader" 2>/dev/null || true
exit 0
