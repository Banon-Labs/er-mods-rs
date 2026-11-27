# OPA unit tests for no_unmeasured_estimate (the Stop-event halt on an unmeasured forecast of time or
# effort). Run with:
#   opa test .cupcake/policies/claude/no_unmeasured_estimate.rego \
#     .cupcake/tests/no_unmeasured_estimate_test.rego
package cupcake.policies.claude.no_unmeasured_estimate_test

import rego.v1

import data.cupcake.policies.claude.no_unmeasured_estimate as guard

effort := "UNMEASURED|kind=effort|forecast=Yes, and it's nearly free."

runtime := "UNMEASURED|kind=runtime|forecast=r10 is running from sim-release (bdj5lkbhk, about 7 hours, tailed in a kitty tab)."

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_unmeasured_estimate": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_effort_string_signal if {
	halts := guard.halt with input as stop_event(effort)
	"ER-EFFECTS-NO-UNMEASURED-ESTIMATE" in rule_ids(halts)
}

test_halt_on_runtime_object_signal if {
	halts := guard.halt with input as stop_event({"output": runtime, "exit_code": 0})
	"ER-EFFECTS-NO-UNMEASURED-ESTIMATE" in rule_ids(halts)
}

test_effort_reason_says_unmeasurable_and_quotes if {
	halts := guard.halt with input as stop_event(effort)
	some d in halts
	contains(d.reason, "sizes development work")
	contains(d.reason, "Yes, and it's nearly free.")
	contains(d.reason, "time the smallest sample")
	contains(d.reason, "Forecasts of time and effort are claims, cited or not made")
}

test_runtime_reason_asks_for_a_measured_number if {
	halts := guard.halt with input as stop_event(runtime)
	some d in halts
	contains(d.reason, "states no measured number")
	contains(d.reason, "about 7 hours")
	contains(d.reason, "Time a sample first")
	contains(d.reason, "say so and give no number")
}

test_unknown_kind_reads_as_runtime if {
	halts := guard.halt with input as stop_event("UNMEASURED|kind=other|forecast=x")
	some d in halts
	contains(d.reason, "Time a sample first")
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_other_event if {
	halts := guard.halt with input as {"hook_event_name": "PreToolUse", "signals": {"last_assistant_unmeasured_estimate": effort}}
	count(halts) == 0
}
