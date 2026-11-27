# OPA unit tests for no_negated_opener (the Stop-event halt on a reply that opens with a non-event).
# Run with:
#   opa test .cupcake/system/commands.rego \
#     .cupcake/policies/claude/no_negated_opener.rego \
#     .cupcake/tests/no_negated_opener_test.rego
package cupcake.policies.claude.no_negated_opener_test

import rego.v1

import data.cupcake.policies.claude.no_negated_opener as guard

facts := "NEGOPENER|question=What does this mean to me?|opener=Nothing in the game changed."

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_negated_opener": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_facts_string_signal if {
	halts := guard.halt with input as stop_event(facts)
	"ER-EFFECTS-NO-NEGATED-OPENER" in rule_ids(halts)
}

test_halt_on_facts_object_signal if {
	halts := guard.halt with input as stop_event({"output": facts, "exit_code": 0})
	"ER-EFFECTS-NO-NEGATED-OPENER" in rule_ids(halts)
}

test_reason_quotes_question_and_opener if {
	halts := guard.halt with input as stop_event(facts)
	some d in halts
	contains(d.reason, "What does this mean to me?")
	contains(d.reason, "Nothing in the game changed.")
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_other_event if {
	halts := guard.halt with input as {"hook_event_name": "PreToolUse", "signals": {"last_assistant_negated_opener": facts}}
	count(halts) == 0
}
