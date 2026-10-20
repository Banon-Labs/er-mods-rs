# OPA unit tests for no_untailed_long_run. Run with:
#   opa test .cupcake/policies/claude/no_untailed_long_run.rego \
#     .cupcake/tests/no_untailed_long_run_test.rego
package cupcake.policies.claude.no_untailed_long_run_test

import rego.v1

import data.cupcake.policies.claude.no_untailed_long_run as guard

boards := "UNTAILEDRUN:python3 scripts/gen-r3-weapon-boards.py --jobs 3 => python3 scripts/kitty-tail-task.py --boards --title boards --file /tmp/t/b1.output"

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_untailed_long_run": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_tagged_signal if {
	halts := guard.halt with input as stop_event(boards)
	"ER-EFFECTS-NO-UNTAILED-LONG-RUN" in rule_ids(halts)
}

test_reason_names_command_and_tail if {
	halts := guard.halt with input as stop_event(boards)
	some d in halts
	contains(d.reason, "gen-r3-weapon-boards.py --jobs 3")
	contains(d.reason, "kitty-tail-task.py --boards")
	contains(d.reason, "/tmp/t/b1.output")
}

test_halt_on_object_signal if {
	halts := guard.halt with input as {
		"hook_event_name": "Stop",
		"signals": {"last_assistant_untailed_long_run": {"output": boards, "exit_code": 0}},
	}
	"ER-EFFECTS-NO-UNTAILED-LONG-RUN" in rule_ids(halts)
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_bare_tag if {
	halts := guard.halt with input as stop_event("UNTAILEDRUN:  ")
	count(halts) == 0
}

test_no_halt_on_untagged_signal if {
	halts := guard.halt with input as stop_event("something else")
	count(halts) == 0
}

test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "PreToolUse",
		"signals": {"last_assistant_untailed_long_run": boards},
	}
	count(halts) == 0
}
