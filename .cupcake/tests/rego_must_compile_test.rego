# OPA unit tests for rego_must_compile. Run with:
#   opa test .cupcake/policies/claude/rego_must_compile.rego .cupcake/tests/rego_must_compile_test.rego
package cupcake.policies.claude.rego_must_compile_test

import rego.v1

import data.cupcake.policies.claude.rego_must_compile as guard

replay_error := concat("", [
	"REGOCHECK\terror\t/home/banon/projects/er-mods-rs/.cupcake/policies/claude/no_stop_waiting_agent.rego\n",
	"1 error occurred: <engine set>/claude/no_stop_waiting_agent.rego:40: rego_type_error: undefined function value_of",
])

event(tool, sig) := {
	"hook_event_name": "PreToolUse",
	"tool_name": tool,
	"tool_input": {},
	"signals": {"rego_compile_check": sig},
}

rule_ids(denials) := {d.rule_id | some d in denials}

test_deny_replay_of_undefined_function if {
	denials := guard.deny with input as event("Edit", replay_error)
	"ER-EFFECTS-REGO-MUST-COMPILE" in rule_ids(denials)
}

test_reason_quotes_opa_and_names_the_file if {
	denials := guard.deny with input as event("Write", replay_error)
	some d in denials
	contains(d.reason, "undefined function value_of")
	contains(d.reason, ".cupcake/policies/claude/no_stop_waiting_agent.rego")
	contains(d.reason, "every tool call in every session")
}

test_deny_on_bash_and_multiedit if {
	count(guard.deny) == 1 with input as event("Bash", replay_error)
	count(guard.deny) == 1 with input as event("MultiEdit", replay_error)
}

test_deny_on_object_signal if {
	denials := guard.deny with input as event("Write", {"output": replay_error, "exit_code": 0})
	"ER-EFFECTS-REGO-MUST-COMPILE" in rule_ids(denials)
}

test_header_only_still_denies_with_placeholder_error if {
	denials := guard.deny with input as event("Write", "REGOCHECK\terror\t/x/.cupcake/system/a.rego")
	some d in denials
	contains(d.reason, "/x/.cupcake/system/a.rego")
	contains(d.reason, "opa printed no error text")
}

test_allow_when_unchecked if {
	count(guard.deny) == 0 with input as event("Write", "REGOCHECK\tunchecked\t/x/.cupcake/policies/claude/a.rego\ncupcake or opa is not on PATH")
}

test_allow_on_empty_signal if {
	count(guard.deny) == 0 with input as event("Edit", "")
}

test_allow_without_signal if {
	count(guard.deny) == 0 with input as {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {}}
}

test_allow_on_other_tool if {
	count(guard.deny) == 0 with input as event("Read", replay_error)
}

test_allow_on_other_event if {
	count(guard.deny) == 0 with input as {
		"hook_event_name": "PostToolUse",
		"tool_name": "Write",
		"signals": {"rego_compile_check": replay_error},
	}
}
