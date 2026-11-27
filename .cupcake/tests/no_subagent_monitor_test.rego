# OPA unit tests for no_subagent_monitor.
#
# Not loaded by the cupcake engine. Run with:
#   opa test .cupcake/policies/claude/no_subagent_monitor.rego \
#            .cupcake/tests/no_subagent_monitor_test.rego
# Listed in ORPHANED_REGO_SUITES in scripts/test-cupcake-policies.py, which also runs the
# engine end to end on a subagent Monitor event.
package cupcake.policies.claude.no_subagent_monitor_test

import rego.v1

import data.cupcake.policies.claude.no_subagent_monitor as guard

RULE := "ER-EFFECTS-NO-SUBAGENT-MONITOR"

monitor_event := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Monitor",
	"tool_input": {"command": "tail -F /x/run.log | python3 scripts/monitor-throttle.py 10", "description": "t"},
}

denied(event) if {
	some d in guard.deny with input as event
	d.rule_id == RULE
}

with_signal(event, value) := object.union(event, {"signals": {"subagent_id": value}})

# The 2026-11-14 shape: a subagent arming a throttled Monitor on its own run's log.
test_deny_subagent_monitor if {
	denied(with_signal(monitor_event, "a8748dea441b1674d\n"))
}

# Both shapes cupcake has delivered a signal in: a bare string and an object with `output`.
test_deny_subagent_monitor_output_object if {
	denied(with_signal(monitor_event, {"output": "a8748dea441b1674d"}))
}

# The main session's events carry no agent_id, so the signal prints nothing; its Monitors stay
# under monitor_rate_limit only.
test_allow_main_session_monitor if {
	not denied(with_signal(monitor_event, ""))
}

test_allow_missing_signal if {
	not denied(monitor_event)
}

# A subagent's background Bash wait is the replacement and stays allowed.
test_allow_subagent_bash if {
	not denied(with_signal({
		"hook_event_name": "PreToolUse",
		"tool_name": "Bash",
		"tool_input": {"command": "until [ -e /x/done ]; do sleep 30; done", "run_in_background": true},
	}, "a8748dea441b1674d"))
}
