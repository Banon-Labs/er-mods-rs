# OPA unit tests for correction_encoded_first (the Stop halt on a correction acted on before it was
# encoded for future agents). Run with:
#   opa test .cupcake/system/commands.rego \
#     .cupcake/policies/claude/correction_encoded_first.rego \
#     .cupcake/tests/correction_encoded_first_test.rego
package cupcake.policies.claude.correction_encoded_first_test

import rego.v1

import data.cupcake.policies.claude.correction_encoded_first as guard

instance_first := "CORRECTIONFACTS|correction=If I ever see a subagent that is finished but hasn't been closed, I always have VERY HIGH concerns that you consumed its results.|verdict=instance_first|first_instance=TaskStop ac34f26adeea54127|encoded_later=0"

encoded_late := "CORRECTIONFACTS|correction=Why did you stash them?|verdict=instance_first|first_instance=Bash git stash pop|encoded_later=1"

unencoded := "CORRECTIONFACTS|correction=I didn't say to stop|verdict=unencoded|first_instance=|encoded_later=0"

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_correction_encoded_first": sig},
}

reasons(sig) := {d.reason | some d in guard.halt with input as stop_event(sig)}

test_halt_on_instance_first if {
	some d in guard.halt with input as stop_event(instance_first)
	d.rule_id == "ER-EFFECTS-CORRECTION-ENCODED-FIRST"
}

test_reason_opens_with_the_marker_the_signal_looks_for if {
	some r in reasons(instance_first)
	startswith(r, "Correction not encoded first")
}

test_reason_names_correction_and_first_instance if {
	some r in reasons(instance_first)
	contains(r, "VERY HIGH concerns")
	contains(r, "TaskStop ac34f26adeea54127")
	contains(r, "every time I correct you")
}

test_reason_for_late_encoding_says_the_turn_may_end if {
	some r in reasons(encoded_late)
	contains(r, "before you encoded it")
	contains(r, "may end after this halt")
}

test_reason_for_unencoded_turn if {
	some r in reasons(unencoded)
	contains(r, "ended without encoding it")
}

test_halt_on_object_signal if {
	halts := guard.halt with input as stop_event({"output": unencoded, "exit_code": 0})
	count(halts) == 1
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_other_event if {
	halts := guard.halt with input as {
		"hook_event_name": "UserPromptSubmit",
		"signals": {"last_assistant_correction_encoded_first": instance_first},
	}
	count(halts) == 0
}
