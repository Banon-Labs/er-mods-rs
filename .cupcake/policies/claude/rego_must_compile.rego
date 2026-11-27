# METADATA
# scope: package
# title: A write to a policy file must leave the engine's policy set compiling
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-REGO-MUST-COMPILE
#   description: >-
#     2026-11-11: a subagent's Edit made .cupcake/policies/claude/no_stop_waiting_agent.rego call
#     `value_of` one Edit before any edit defined it. Cupcake compiles every policy before it runs
#     any, so it stopped starting ("rego_type_error: undefined function value_of"), every tool call
#     in every session on the checkout was refused -- the Edit that would have defined the function
#     included -- and the user had to delete the file by hand.
#
#     The `rego_compile_check` signal (`scripts/cupcake_rego_compile_check.py`) builds the content a
#     Write, Edit, MultiEdit or resolvable Bash write would leave in a .rego under a .cupcake
#     directory, puts it in a copy of that directory, lets cupcake itself pick the files it would
#     compile, and runs `opa check` on exactly that set. This policy denies the write when the set
#     would not compile and quotes opa. A file the engine does not load (tests/, signals/, a policy
#     with no METADATA block) is not judged. When the check cannot run (no opa or cupcake, a
#     timeout) the write is allowed.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Write", "Edit", "MultiEdit", "Bash"]
#     required_signals: ["rego_compile_check"]
package cupcake.policies.claude.rego_must_compile

import rego.v1

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name in {"Write", "Edit", "MultiEdit", "Bash"}
	startswith(raw, "REGOCHECK\terror\t")
	decision := {
		"rule_id": "ER-EFFECTS-REGO-MUST-COMPILE",
		"reason": concat("", [
			"This write leaves the policy set cupcake compiles broken: ", target, ". ",
			"opa check, run on the exact set the engine would build with this write in place, says:\n\n",
			errors,
			"\n\nA policy set that does not compile stops cupcake before any guard runs: every tool call in every session on that checkout is refused, the edit that would repair it included, and only the user can undo it. ",
			"On 2026-11-11 an Edit that called value_of one Edit before value_of was defined did exactly that. ",
			"Make every single write leave the file compiling: write the whole file with one Write, or define a new function in the same Edit as its first call, or in an earlier one.",
		]),
		"severity": "HIGH",
	}
}

raw := v if {
	v := input.signals.rego_compile_check
	is_string(v)
} else := v if {
	v := input.signals.rego_compile_check.output
	is_string(v)
} else := ""

header := h if {
	i := indexof(raw, "\n")
	i >= 0
	h := substring(raw, 0, i)
} else := raw

target := t if {
	parts := split(header, "\t")
	count(parts) >= 3
	t := parts[2]
} else := "a .rego file under .cupcake"

errors := e if {
	i := indexof(raw, "\n")
	i >= 0
	e := trim(substring(raw, i + 1, -1), " \t\r\n")
} else := "(opa printed no error text)"
