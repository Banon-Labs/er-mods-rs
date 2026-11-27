# OPA unit tests for no_untailed_agent. Run with:
#   opa test .cupcake/policies/claude/no_untailed_agent.rego \
#     .cupcake/tests/no_untailed_agent_test.rego
package cupcake.policies.claude.no_untailed_agent_test

import rego.v1

import data.cupcake.policies.claude.no_untailed_agent as guard

round2 := "UNTAILEDAGENT:a505613c401ed9d43 (\"Speed up attack sets, round 2\") => python3 scripts/kitty-tail-agent.py a505613c401ed9d43"

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_untailed_agent": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_tagged_signal if {
	halts := guard.halt with input as stop_event(round2)
	"ER-EFFECTS-NO-UNTAILED-AGENT" in rule_ids(halts)
}

test_reason_names_agent_and_command if {
	halts := guard.halt with input as stop_event(round2)
	some d in halts
	contains(d.reason, "a505613c401ed9d43 (\"Speed up attack sets, round 2\")")
	contains(d.reason, "python3 scripts/kitty-tail-agent.py a505613c401ed9d43")
	contains(d.reason, "without me being able to see the output")
}

test_halt_on_object_signal if {
	halts := guard.halt with input as {
		"hook_event_name": "Stop",
		"signals": {"last_assistant_untailed_agent": {"output": round2, "exit_code": 0}},
	}
	"ER-EFFECTS-NO-UNTAILED-AGENT" in rule_ids(halts)
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_bare_tag if {
	halts := guard.halt with input as stop_event("UNTAILEDAGENT:  ")
	count(halts) == 0
}

test_no_halt_on_untagged_signal if {
	halts := guard.halt with input as stop_event("something else")
	count(halts) == 0
}

test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "PreToolUse",
		"signals": {"last_assistant_untailed_agent": round2},
	}
	count(halts) == 0
}
