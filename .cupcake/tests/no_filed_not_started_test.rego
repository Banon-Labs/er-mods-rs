# OPA unit tests for no_filed_not_started (the Stop-event halt on work filed and not started).
# Run with:
#   opa test .cupcake/system/commands.rego \
#     .cupcake/policies/claude/no_filed_not_started.rego \
#     .cupcake/tests/no_filed_not_started_test.rego
package cupcake.policies.claude.no_filed_not_started_test

import rego.v1

import data.cupcake.policies.claude.no_filed_not_started as guard

facts := "FILEDFACTS|filed=1|closing=The fix is filed as bd er-effects-rs-me5p."

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_filed_not_started": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_facts_string_signal if {
	halts := guard.halt with input as stop_event(facts)
	"ER-EFFECTS-NO-FILED-NOT-STARTED" in rule_ids(halts)
}

test_halt_on_facts_object_signal if {
	halts := guard.halt with input as stop_event({"output": facts, "exit_code": 0})
	"ER-EFFECTS-NO-FILED-NOT-STARTED" in rule_ids(halts)
}

test_reason_quotes_closing if {
	halts := guard.halt with input as stop_event(facts)
	some d in halts
	contains(d.reason, "er-effects-rs-me5p")
}

deferred := "FILEDFACTS|kind=deferred|closing=and I'll apply it when the kit run (b3fgttzcf) ends."

test_halt_on_deferred_fix if {
	halts := guard.halt with input as stop_event(deferred)
	"ER-EFFECTS-NO-FILED-NOT-STARTED" in rule_ids(halts)
}

test_deferred_reason_names_running_job_and_quotes_closing if {
	halts := guard.halt with input as stop_event(deferred)
	some d in halts
	contains(d.reason, "parks a fix behind a running job")
	contains(d.reason, "b3fgttzcf")
	not contains(d.reason, "You filed work this turn")
}

test_filed_reason_kept_for_explicit_kind if {
	halts := guard.halt with input as stop_event("FILEDFACTS|kind=filed|filed=1|closing=Filed.")
	some d in halts
	contains(d.reason, "You filed work this turn")
}

test_unknown_kind_does_not_halt if {
	halts := guard.halt with input as stop_event("FILEDFACTS|kind=other|closing=x")
	count(halts) == 0
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_other_event if {
	halts := guard.halt with input as {"hook_event_name": "PreToolUse", "signals": {"last_assistant_filed_not_started": facts}}
	count(halts) == 0
}
