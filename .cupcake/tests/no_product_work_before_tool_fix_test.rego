# OPA unit tests for no_product_work_before_tool_fix.
# Run with:
#   opa test .cupcake/policies/claude/no_product_work_before_tool_fix.rego \
#     .cupcake/tests/no_product_work_before_tool_fix_test.rego
package cupcake.policies.claude.no_product_work_before_tool_fix_test

import rego.v1

import data.cupcake.policies.claude.no_product_work_before_tool_fix as guard

reported_line := "TOOLMISS|reported=1|prompt=Multiple rego violations uncaught in this response"

dispatch(signal, prompt) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Agent",
	"tool_input": {"description": "work", "prompt": prompt},
	"signals": {"tool_miss_report": signal},
}

# The 2026-10-18 digression: the build-kit defects dispatched while the guard miss was open.
test_deny_product_dispatch_after_reported_miss if {
	input_event := dispatch(reported_line, "Fix the crit score so it counts only stance-break ripostes and backstabs, and measure claw pairings in scripts/er-builds-optimize.py.")
	ids := {d.rule_id | some d in guard.deny with input as input_event}
	"ER-EFFECTS-TOOLS-BEFORE-PROBLEMS" in ids
}

test_deny_quotes_the_report if {
	input_event := dispatch(reported_line, "Fix the crit score.")
	some d in guard.deny with input as input_event
	contains(d.reason, "Multiple rego violations uncaught")
}

# The guard dispatch the user asked for.
test_allow_guard_dispatch_after_reported_miss if {
	input_event := dispatch(reported_line, "Work on the Cupcake stop-hook guards only (.cupcake/policies/claude/*.rego, .cupcake/signals/*.sh).")
	count(guard.deny) == 0 with input as input_event
}

test_allow_task_tool_alias_on_guard_work if {
	input_event := object.union(dispatch(reported_line, "widen the rego detector"), {"tool_name": "Task"})
	count(guard.deny) == 0 with input as input_event
}

test_deny_task_tool_alias_on_product_work if {
	input_event := object.union(dispatch(reported_line, "rebuild the DLL"), {"tool_name": "Task"})
	count(guard.deny) == 1 with input as input_event
}

test_allow_any_dispatch_without_a_report if {
	input_event := dispatch("TOOLMISS|reported=0|prompt=fix the crit score", "Fix the crit score.")
	count(guard.deny) == 0 with input as input_event
}

# Fails open: an empty or garbled signal never refuses a dispatch.
test_allow_on_empty_signal if {
	input_event := dispatch("", "Fix the crit score.")
	count(guard.deny) == 0 with input as input_event
}

test_allow_on_garbled_signal if {
	input_event := dispatch("reported=1", "Fix the crit score.")
	count(guard.deny) == 0 with input as input_event
}

test_ignores_other_tools if {
	input_event := object.union(dispatch(reported_line, "Fix the crit score."), {"tool_name": "Bash"})
	count(guard.deny) == 0 with input as input_event
}
