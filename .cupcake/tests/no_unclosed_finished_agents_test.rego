# OPA unit tests for no_unclosed_finished_agents. Run with:
#   opa test .cupcake/policies/claude/no_unclosed_finished_agents.rego \
#     .cupcake/tests/no_unclosed_finished_agents_test.rego
package cupcake.policies.claude.no_unclosed_finished_agents_test

import rego.v1

import data.cupcake.policies.claude.no_unclosed_finished_agents as guard

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_unclosed_finished_agents": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_tagged_signal if {
	halts := guard.halt with input as stop_event("UNCLOSEDAGENTS:a4d14f2d4d71a5ba0 (\"Model release frame unknown to defender\")")
	"ER-EFFECTS-NO-UNCLOSED-FINISHED-AGENTS" in rule_ids(halts)
}

test_reason_names_agent_description_and_taskstop if {
	halts := guard.halt with input as stop_event("UNCLOSEDAGENTS:a4d14f2d4d71a5ba0 (\"Model release frame unknown to defender\")")
	some d in halts
	contains(d.reason, "a4d14f2d4d71a5ba0")
	contains(d.reason, "Model release frame unknown to defender")
	contains(d.reason, "TaskStop")
	contains(d.reason, "consume its result")
}

test_reason_says_a_waiting_agent_is_not_closed if {
	halts := guard.halt with input as stop_event("UNCLOSEDAGENTS:a4d14f2d4d71a5ba0 (\"Model release frame unknown to defender\")")
	some d in halts
	contains(d.reason, "waiting on its own background work is not closed until it finishes")
}

test_halt_on_object_signal if {
	halts := guard.halt with input as {
		"hook_event_name": "Stop",
		"signals": {"last_assistant_unclosed_finished_agents": {"output": "UNCLOSEDAGENTS:ab12 (\"x\")", "exit_code": 0}},
	}
	"ER-EFFECTS-NO-UNCLOSED-FINISHED-AGENTS" in rule_ids(halts)
}

test_one_decision_for_several_agents if {
	halts := guard.halt with input as stop_event("UNCLOSEDAGENTS:ab12 (\"x\"); cd34 (\"y\")")
	count(halts) == 1
	some d in halts
	contains(d.reason, "ab12")
	contains(d.reason, "cd34")
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_bare_tag if {
	halts := guard.halt with input as stop_event("UNCLOSEDAGENTS:  ")
	count(halts) == 0
}

test_no_halt_on_untagged_signal if {
	halts := guard.halt with input as stop_event("UNEXPLAINEDTASKS:b31ydejza (bash: x)")
	count(halts) == 0
}

test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "PreToolUse",
		"signals": {"last_assistant_unclosed_finished_agents": "UNCLOSEDAGENTS:ab12 (\"x\")"},
	}
	count(halts) == 0
}
