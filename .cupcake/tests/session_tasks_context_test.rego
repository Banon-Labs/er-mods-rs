# OPA unit tests for session_tasks_context (the UserPromptSubmit report of this session's running
# shells and agents). Run with:
#   opa test .cupcake/policies/claude/session_tasks_context.rego \
#     .cupcake/tests/session_tasks_context_test.rego
package cupcake.policies.claude.session_tasks_context_test

import rego.v1

import data.cupcake.policies.claude.session_tasks_context as guard

report := "Shells running: bx12 (agent a740eb3 (Speed up): Rerun the selftest; up 3m02s). Agents working: a740eb3 'Speed up' (running)."

ups_event(sig) := {
	"hook_event_name": "UserPromptSubmit",
	"signals": {"session_tasks": sig},
}

test_context_carries_the_report if {
	ctxs := guard.add_context with input as ups_event(report)
	count(ctxs) == 1
	some c in ctxs
	startswith(c, "RUNNING NOW in this session")
	contains(c, "bx12 (agent a740eb3 (Speed up): Rerun the selftest; up 3m02s)")
	contains(c, "names the shell's task id or the agent it is blocked on")
}

test_context_on_object_signal if {
	ctxs := guard.add_context with input as ups_event({"output": report, "exit_code": 0})
	count(ctxs) == 1
}

# Nothing running: the helper prints nothing, and nothing is injected.
test_no_context_on_empty_signal if {
	ctxs := guard.add_context with input as ups_event("")
	count(ctxs) == 0
}

test_no_context_on_whitespace_signal if {
	ctxs := guard.add_context with input as ups_event(" \n")
	count(ctxs) == 0
}

test_no_context_on_other_events if {
	ctxs := guard.add_context with input as {"hook_event_name": "Stop", "signals": {"session_tasks": report}}
	count(ctxs) == 0
}
