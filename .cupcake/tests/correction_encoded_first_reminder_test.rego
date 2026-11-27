# OPA unit tests for correction_encoded_first_reminder (the UserPromptSubmit line that tells a turn
# opened by a correction to encode it for future agents first). Run with:
#   opa test .cupcake/system/commands.rego \
#     .cupcake/policies/claude/correction_encoded_first_reminder.rego \
#     .cupcake/tests/correction_encoded_first_reminder_test.rego
package cupcake.policies.claude.correction_encoded_first_reminder_test

import rego.v1

import data.cupcake.policies.claude.correction_encoded_first_reminder as guard

ups_event(sig) := {
	"hook_event_name": "UserPromptSubmit",
	"signals": {"user_prompt_correction": sig},
}

test_context_on_correction if {
	ctxs := guard.add_context with input as ups_event("CORRECTION|I didn't say to stop")
	count(ctxs) == 1
	some c in ctxs
	startswith(c, "CORRECTION FIRST:")
	contains(c, "I didn't say to stop")
	contains(c, "before any action on the instance")
}

test_context_on_object_signal if {
	ctxs := guard.add_context with input as ups_event({"output": "CORRECTION|Why did you stash them?", "exit_code": 0})
	count(ctxs) == 1
}

test_no_context_on_empty_signal if {
	ctxs := guard.add_context with input as ups_event("")
	count(ctxs) == 0
}

test_no_context_on_untagged_signal if {
	ctxs := guard.add_context with input as ups_event("run r10")
	count(ctxs) == 0
}

test_no_context_on_stop_event if {
	ctxs := guard.add_context with input as {
		"hook_event_name": "Stop",
		"signals": {"user_prompt_correction": "CORRECTION|I didn't say to stop"},
	}
	count(ctxs) == 0
}
