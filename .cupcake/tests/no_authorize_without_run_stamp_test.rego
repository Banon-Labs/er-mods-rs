# OPA unit tests for no_authorize_without_run_stamp. Run with:
#   opa test .cupcake/policies/claude/no_authorize_without_run_stamp.rego \
#     .cupcake/tests/no_authorize_without_run_stamp_test.rego
package cupcake.policies.claude.no_authorize_without_run_stamp_test

import rego.v1

import data.cupcake.policies.claude.no_authorize_without_run_stamp as guard

tagged := "AUTHNORUNSTAMP:b7f475f2d9e5\t/home/banon/projects/er-mods-rs/.worktrees/er-duel-rules\tthe newest log er-quickload-autoload-debug.log (er_quickload.dll) was built from 6c7346cd, not b7f475f2"

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_authorize_without_run_stamp": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_tagged_signal if {
	halts := guard.halt with input as stop_event(tagged)
	"ER-EFFECTS-NO-AUTHORIZE-WITHOUT-RUN-STAMP" in rule_ids(halts)
}

test_reason_names_sha_checkout_and_note if {
	halts := guard.halt with input as stop_event(tagged)
	some d in halts
	contains(d.reason, "`authorize b7f475f2d9e5`")
	contains(d.reason, "in /home/banon/projects/er-mods-rs/.worktrees/er-duel-rules")
	contains(d.reason, "was built from 6c7346cd, not b7f475f2")
}

test_reason_names_the_stamping_steps if {
	halts := guard.halt with input as stop_event(tagged)
	some d in halts
	contains(d.reason, "scripts/er-build-dlls.sh <package>")
	contains(d.reason, "scripts/er-run-branch.py --with <package>")
	contains(d.reason, "bash scripts/check-runtime-evidence.sh b7f475f2d9e5` exits 0")
	contains(d.reason, "Only then ask for the authorization")
}

test_halt_on_object_signal if {
	halts := guard.halt with input as {
		"hook_event_name": "Stop",
		"signals": {"last_assistant_authorize_without_run_stamp": {"output": tagged, "exit_code": 0}},
	}
	"ER-EFFECTS-NO-AUTHORIZE-WITHOUT-RUN-STAMP" in rule_ids(halts)
}

test_missing_fields_fall_back if {
	halts := guard.halt with input as stop_event("AUTHNORUNSTAMP:b7f475f2d9e5")
	some d in halts
	contains(d.reason, "`authorize b7f475f2d9e5`")
	contains(d.reason, "in its checkout")
	contains(d.reason, "no DLL log names this commit")
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_whitespace_signal if {
	halts := guard.halt with input as stop_event("  \n")
	count(halts) == 0
}

test_no_halt_on_absent_signal if {
	halts := guard.halt with input as {"hook_event_name": "Stop", "signals": {}}
	count(halts) == 0
}

test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "PreToolUse",
		"signals": {"last_assistant_authorize_without_run_stamp": tagged},
	}
	count(halts) == 0
}
