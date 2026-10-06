# OPA unit tests for no_ask_without_receiver. Run with:
#   opa test .cupcake/policies/claude/no_ask_without_receiver.rego \
#     .cupcake/tests/no_ask_without_receiver_test.rego
package cupcake.policies.claude.no_ask_without_receiver_test

import rego.v1

import data.cupcake.policies.claude.no_ask_without_receiver as guard

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_ask_without_receiver": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_tagged_signal if {
	halts := guard.halt with input as stop_event("ASKNORECEIVER:I need you to close the item list with B twice")
	"ER-EFFECTS-NO-ASK-WITHOUT-RECEIVER" in rule_ids(halts)
}

test_reason_quotes_sentence_and_names_monitor if {
	halts := guard.halt with input as stop_event("ASKNORECEIVER:I need you to close the item list with B twice")
	some d in halts
	contains(d.reason, "close the item list with B twice")
	contains(d.reason, "Monitor")
}

test_halt_on_object_signal if {
	halts := guard.halt with input as {
		"hook_event_name": "Stop",
		"signals": {"last_assistant_ask_without_receiver": {"output": "ASKNORECEIVER:please press R3", "exit_code": 0}},
	}
	"ER-EFFECTS-NO-ASK-WITHOUT-RECEIVER" in rule_ids(halts)
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_whitespace_signal if {
	halts := guard.halt with input as stop_event("  \n")
	count(halts) == 0
}

test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "PreToolUse",
		"signals": {"last_assistant_ask_without_receiver": "ASKNORECEIVER:please press R3"},
	}
	count(halts) == 0
}
