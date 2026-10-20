# OPA unit tests for no_unexplained_running_tasks. Run with:
#   opa test .cupcake/policies/claude/no_unexplained_running_tasks.rego \
#     .cupcake/tests/no_unexplained_running_tasks_test.rego
package cupcake.policies.claude.no_unexplained_running_tasks_test

import rego.v1

import data.cupcake.policies.claude.no_unexplained_running_tasks as guard

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_unexplained_running_tasks": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_tagged_signal if {
	halts := guard.halt with input as stop_event("UNEXPLAINEDTASKS:b31ydejza (bash: while true; do cp ...)")
	"ER-EFFECTS-NO-UNEXPLAINED-RUNNING-TASKS" in rule_ids(halts)
}

test_reason_names_task_and_taskstop if {
	halts := guard.halt with input as stop_event("UNEXPLAINEDTASKS:b31ydejza (bash: while true; do cp ...)")
	some d in halts
	contains(d.reason, "b31ydejza")
	contains(d.reason, "TaskStop")
}

test_halt_on_object_signal if {
	halts := guard.halt with input as {
		"hook_event_name": "Stop",
		"signals": {"last_assistant_unexplained_running_tasks": {"output": "UNEXPLAINEDTASKS:bm1 (monitor: tail -F x)", "exit_code": 0}},
	}
	"ER-EFFECTS-NO-UNEXPLAINED-RUNNING-TASKS" in rule_ids(halts)
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_bare_tag if {
	halts := guard.halt with input as stop_event("UNEXPLAINEDTASKS:  ")
	count(halts) == 0
}

test_no_halt_on_untagged_signal if {
	halts := guard.halt with input as stop_event("something else")
	count(halts) == 0
}

test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "PreToolUse",
		"signals": {"last_assistant_unexplained_running_tasks": "UNEXPLAINEDTASKS:bm1 (monitor: tail -F x)"},
	}
	count(halts) == 0
}
