# OPA unit tests for no_unwatched_ci_claim. Run with:
#   opa test .cupcake/policies/claude/no_unwatched_ci_claim.rego \
#     .cupcake/tests/no_unwatched_ci_claim_test.rego
package cupcake.policies.claude.no_unwatched_ci_claim_test

import rego.v1

import data.cupcake.policies.claude.no_unwatched_ci_claim as guard

sig := "UNWATCHEDCI:\"CI on GitHub is still running; `...` and `...` hadn't finished at last check.\" => start `gh pr checks 630 --watch` with run_in_background: true, then open its tail split in the same turn: `python3 scripts/kitty-tail-task.py --title 'CI: PR #630' --file <its output file> --match 'gh pr checks'`"

stop_event(s) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_ci_running_claim": s},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_tagged_signal if {
	halts := guard.halt with input as stop_event(sig)
	"ER-EFFECTS-NO-UNWATCHED-CI-CLAIM" in rule_ids(halts)
}

test_reason_quotes_claim_and_names_watcher_and_tail if {
	halts := guard.halt with input as stop_event(sig)
	some d in halts
	contains(d.reason, "CI on GitHub is still running")
	contains(d.reason, "gh pr checks 630 --watch")
	contains(d.reason, "kitty-tail-task.py")
}

test_halt_on_object_signal if {
	halts := guard.halt with input as {
		"hook_event_name": "Stop",
		"signals": {"last_assistant_ci_running_claim": {"output": sig, "exit_code": 0}},
	}
	"ER-EFFECTS-NO-UNWATCHED-CI-CLAIM" in rule_ids(halts)
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_bare_tag if {
	halts := guard.halt with input as stop_event("UNWATCHEDCI:   ")
	count(halts) == 0
}

test_no_halt_on_untagged_signal if {
	halts := guard.halt with input as stop_event("CI on GitHub is still running")
	count(halts) == 0
}

test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "UserPromptSubmit",
		"signals": {"last_assistant_ci_running_claim": sig},
	}
	count(halts) == 0
}
