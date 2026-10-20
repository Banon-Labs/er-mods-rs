# OPA unit tests for no_unexecuted_promise (the Stop-event halt on a turn that ended with a
# first-person promise nothing is going to keep).
# Run with:
#   opa test .cupcake/system/commands.rego \
#     .cupcake/policies/claude/no_unexecuted_promise.rego \
#     .cupcake/tests/no_unexecuted_promise_test.rego
#
# These pin the POLICY half: signal tag -> halt, and the correction text. The four false-positive
# carve-outs (contingent on the user, directive to the user, executed in-turn, covered by a live
# background task) are decided in the SIGNAL, and are pinned by scripts/test-unexecuted-promise-signal.py
# against crafted transcripts -- an empty signal here stands for every one of them.
package cupcake.policies.claude.no_unexecuted_promise_test

import rego.v1

import data.cupcake.policies.claude.no_unexecuted_promise as guard

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_unexecuted_promise": sig},
}

stop_event_object_signal(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_unexecuted_promise": {"output": sig, "exit_code": 0}},
}

rule_ids(halts) := {d.rule_id | some d in halts}

# A tagged promise hit halts the turn-end.
test_halt_on_promise_tagged_signal if {
	halts := guard.halt with input as stop_event("PROMISE:I'll re-record the directive with the shell metacharacters escaped.")
	"ER-EFFECTS-NO-UNEXECUTED-PROMISE" in rule_ids(halts)
}

# Bare/untagged non-empty value (backward compat with crafted signal shapes) still halts.
test_halt_on_bare_string_signal if {
	halts := guard.halt with input as stop_event("I'll re-run the gate.")
	"ER-EFFECTS-NO-UNEXECUTED-PROMISE" in rule_ids(halts)
}

# Object-shaped signal ({output: ...}) is handled too.
test_halt_on_object_signal if {
	halts := guard.halt with input as stop_event_object_signal("PROMISE:I'll patch the offset.")
	"ER-EFFECTS-NO-UNEXECUTED-PROMISE" in rule_ids(halts)
}

# The correction quotes the offending sentence back, the way idle_hold's does.
test_reason_quotes_the_offending_phrase if {
	halts := guard.halt with input as stop_event("PROMISE:I'll re-record the directive with the shell metacharacters escaped.")
	some d in halts
	contains(d.reason, "I'll re-record the directive with the shell metacharacters escaped.")
}

# The correction names all THREE ways out, concretely.
test_reason_offers_three_ways_out if {
	halts := guard.halt with input as stop_event("PROMISE:I'll re-run the gate.")
	some d in halts
	contains(d.reason, "EXECUTE IT")
	contains(d.reason, "background task")
	contains(d.reason, "REWRITE")
	contains(d.reason, "re-initiate")
}

# Severity is carried so the halt is reported at the same weight as the sibling Stop guards.
test_halt_is_high_severity if {
	halts := guard.halt with input as stop_event("PROMISE:I'll re-run the gate.")
	some d in halts
	d.severity == "HIGH"
}

# The signal's counted form renders the message the way the user reads it: the sentence, with the
# size of the prose on either side.
test_reason_renders_the_skeleton if {
	halts := guard.halt with input as stop_event("PROMISE:120:45:I'll re-run the gate.")
	some d in halts
	contains(d.reason, "<120 characters of prose> 'I'll re-run the gate.' <45 characters of prose>")
	not contains(d.reason, "120:45")
}

# A side with no prose is left out instead of rendered as "<0 characters of prose>".
test_skeleton_omits_an_empty_side if {
	halts := guard.halt with input as stop_event("PROMISE:0:30:I'll re-run the gate.")
	some d in halts
	contains(d.reason, "'I'll re-run the gate.' <30 characters of prose>")
	not contains(d.reason, "<0 characters")
}

# The 2026-10-19 shape: a handoff the reader reaches only after a paragraph. It halts under the same
# rule, and the correction is about moving the ask, not about finding a phrase the regex accepts.
test_buried_handoff_halts_and_says_move_it_first if {
	halts := guard.halt with input as stop_event("BURIED:212:96:Reply authorize 75a188c0 and I'll close the game and push from the clean checkout.")
	"ER-EFFECTS-NO-UNEXECUTED-PROMISE" in rule_ids(halts)
	some d in halts
	contains(d.reason, "<212 characters of prose> 'Reply authorize 75a188c0 and I'll close the game and push from the clean checkout.' <96 characters of prose>")
	contains(d.reason, "FIRST sentence")
	contains(d.reason, "after 212 characters")
	not contains(d.reason, "EXECUTE IT")
}

# Clean turn (the signal found no violation, or found one of the four exemptions) -> no halt.
test_no_halt_on_clean_turn if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

# Whitespace-only signal is treated as clean.
test_no_halt_on_whitespace_signal if {
	halts := guard.halt with input as stop_event("   \n")
	count(halts) == 0
}

# Missing signal entirely (routing not satisfied) -> no halt, no evaluation error.
test_no_halt_when_signal_absent if {
	halts := guard.halt with input as {"hook_event_name": "Stop", "signals": {}}
	count(halts) == 0
}

# The halt only applies to Stop events, not other events that might carry the signal.
test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "PreToolUse",
		"signals": {"last_assistant_unexecuted_promise": "PROMISE:I'll re-run the gate."},
	}
	count(halts) == 0
}
