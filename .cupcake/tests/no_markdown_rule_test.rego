# OPA unit tests for no_markdown_rule (the Stop-event halt on a closing message that calls text in a
# markdown file a rule). Run with:
#   opa test .cupcake/policies/claude/no_markdown_rule.rego .cupcake/tests/no_markdown_rule_test.rego
package cupcake.policies.claude.no_markdown_rule_test

import rego.v1

import data.cupcake.policies.claude.no_markdown_rule as guard

hit := "MDRULE|referent=`docs/agents/runtime-evidence.md`|sentence=The rule is now in `docs/agents/runtime-evidence.md`."

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_markdown_rule": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_string_signal if {
	halts := guard.halt with input as stop_event(hit)
	"ER-EFFECTS-NO-MARKDOWN-RULE" in rule_ids(halts)
}

test_halt_on_object_signal if {
	halts := guard.halt with input as stop_event({"output": hit, "exit_code": 0})
	"ER-EFFECTS-NO-MARKDOWN-RULE" in rule_ids(halts)
}

test_reason_quotes_the_sentence_and_the_referent if {
	halts := guard.halt with input as stop_event(hit)
	some d in halts
	contains(d.reason, "calls text in a markdown file (`docs/agents/runtime-evidence.md`) a rule")
	contains(d.reason, "'The rule is now in `docs/agents/runtime-evidence.md`.'")
	contains(d.reason, "only executable enforcement (a policy, a test, a hook) does")
	contains(d.reason, "'AGENTS.md describes it'")
	contains(d.reason, "put it in quotes")
}

test_missing_referent_reads_as_a_markdown_file if {
	halts := guard.halt with input as stop_event("MDRULE|sentence=AGENTS.md now carries the same rule.")
	some d in halts
	contains(d.reason, "calls text in a markdown file (a markdown file) a rule")
	contains(d.reason, "AGENTS.md now carries the same rule.")
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_whitespace_signal if {
	halts := guard.halt with input as stop_event("  \n")
	count(halts) == 0
}

test_no_halt_on_other_event if {
	halts := guard.halt with input as {"hook_event_name": "PreToolUse", "signals": {"last_assistant_markdown_rule": hit}}
	count(halts) == 0
}
