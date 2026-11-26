# OPA unit tests for no_unwatched_red_stage. Run with:
#   opa test .cupcake/policies/claude/no_unwatched_red_stage.rego \
#     .cupcake/tests/no_unwatched_red_stage_test.rego
package cupcake.policies.claude.no_unwatched_red_stage_test

import rego.v1

import data.cupcake.policies.claude.no_unwatched_red_stage as guard

push := "UNWATCHEDRED:bpush1 (git push: git push -u origin chore/x) => Monitor with timeout_ms 1800000: tail -n +1 -F /tmp/t/bpush1.output | grep --line-buffered -E 'RED|FAIL|error|REFUSED|rejected|exited with code' | python3 /r/scripts/monitor-throttle.py 10"

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_unwatched_red_stage": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_tagged_signal if {
	halts := guard.halt with input as stop_event(push)
	"ER-EFFECTS-NO-UNWATCHED-RED-STAGE" in rule_ids(halts)
}

test_reason_names_task_and_monitor if {
	halts := guard.halt with input as stop_event(push)
	some d in halts
	contains(d.reason, "bpush1")
	contains(d.reason, "tail -n +1 -F /tmp/t/bpush1.output")
	contains(d.reason, "monitor-throttle.py 10")
	contains(d.reason, "read that stage's log")
}

test_halt_on_object_signal if {
	halts := guard.halt with input as {
		"hook_event_name": "Stop",
		"signals": {"last_assistant_unwatched_red_stage": {"output": push, "exit_code": 0}},
	}
	"ER-EFFECTS-NO-UNWATCHED-RED-STAGE" in rule_ids(halts)
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_bare_tag if {
	halts := guard.halt with input as stop_event("UNWATCHEDRED:  ")
	count(halts) == 0
}

test_no_halt_on_untagged_signal if {
	halts := guard.halt with input as stop_event("something else")
	count(halts) == 0
}

test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "PreToolUse",
		"signals": {"last_assistant_unwatched_red_stage": push},
	}
	count(halts) == 0
}
