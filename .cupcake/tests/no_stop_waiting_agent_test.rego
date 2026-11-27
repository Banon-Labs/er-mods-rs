# OPA unit tests for no_stop_waiting_agent. Run with:
#   opa test .cupcake/policies/claude/no_stop_waiting_agent.rego \
#     .cupcake/tests/no_stop_waiting_agent_test.rego
package cupcake.policies.claude.no_stop_waiting_agent_test

import rego.v1

import data.cupcake.policies.claude.no_stop_waiting_agent as guard

waiting_line := concat("", [
	"WAITINGAGENT|id=abce87a3bda959d98|override=0",
	"|description=Strip losing rules, merge dnok, test",
	"|prompt=My largest concern is that every time I correct you, you fix the issue",
])

stop_event(sig) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "TaskStop",
	"tool_input": {"task_id": "abce87a3bda959d98"},
	"signals": {"task_stop_waiting_agent": sig},
}

rule_ids(denials) := {d.rule_id | some d in denials}

test_deny_stop_of_waiting_agent if {
	denials := guard.deny with input as stop_event(waiting_line)
	"ER-EFFECTS-NO-STOP-WAITING-AGENT" in rule_ids(denials)
}

test_reason_says_not_finished_work_dies_and_finished_notification if {
	denials := guard.deny with input as stop_event(waiting_line)
	some d in denials
	contains(d.reason, "abce87a3bda959d98")
	contains(d.reason, "Strip losing rules, merge dnok, test")
	contains(d.reason, "is not finished")
	contains(d.reason, "kills that work")
	contains(d.reason, "notify \"finished\"")
}

test_deny_with_trailing_newline_and_object_signal if {
	denials := guard.deny with input as {
		"hook_event_name": "PreToolUse",
		"tool_name": "TaskStop",
		"signals": {"task_stop_waiting_agent": {"output": concat("", [waiting_line, "\n"]), "exit_code": 0}},
	}
	"ER-EFFECTS-NO-STOP-WAITING-AGENT" in rule_ids(denials)
}

test_deny_without_description if {
	denials := guard.deny with input as stop_event("WAITINGAGENT|id=abce87a3bda959d98|override=0")
	"ER-EFFECTS-NO-STOP-WAITING-AGENT" in rule_ids(denials)
}

test_allow_when_user_overrides if {
	sig := replace(waiting_line, "override=0", "override=1")
	count(guard.deny) == 0 with input as stop_event(sig)
}

test_allow_on_empty_signal if {
	count(guard.deny) == 0 with input as stop_event("")
}

test_allow_without_signal if {
	count(guard.deny) == 0 with input as {"hook_event_name": "PreToolUse", "tool_name": "TaskStop"}
}

test_allow_on_untagged_signal if {
	count(guard.deny) == 0 with input as stop_event("UNCLOSEDAGENTS:abce87a3bda959d98")
}

test_allow_on_other_tool if {
	count(guard.deny) == 0 with input as {
		"hook_event_name": "PreToolUse",
		"tool_name": "Bash",
		"signals": {"task_stop_waiting_agent": waiting_line},
	}
}

test_allow_on_other_event if {
	count(guard.deny) == 0 with input as {
		"hook_event_name": "PostToolUse",
		"tool_name": "TaskStop",
		"signals": {"task_stop_waiting_agent": waiting_line},
	}
}
