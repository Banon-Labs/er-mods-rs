# METADATA
# scope: package
# title: When the user's message reads as a correction, say that its first change encodes it
# authors: ["er-quickload agents"]
# custom:
#   severity: LOW
#   id: ER-EFFECTS-CORRECTION-ENCODED-FIRST-REMINDER
#   description: >-
#     UserPromptSubmit half of correction_encoded_first.rego. User directive 2026-11-11, verbatim:
#     "My largest concern is that every time I correct you, you fix the issue instead of fix it for
#     future agents first". When the prompt being submitted reads as a correction
#     (scripts/cupcake_correction_first.correction, via the user_prompt_correction signal), inject
#     one line telling the turn that its first change must encode the correction for future agents,
#     before any action on the instance. The Stop guard halts the turn that does not.
#   routing:
#     required_events: ["UserPromptSubmit"]
#     required_signals: ["user_prompt_correction"]
package cupcake.policies.claude.correction_encoded_first_reminder

import rego.v1

add_context contains context if {
	input.hook_event_name == "UserPromptSubmit"
	sentence != ""
	context := concat("", [
		"CORRECTION FIRST: this message reads as a correction ('",
		sentence,
		"'). The turn's first change must encode it for future agents -- executable enforcement (a guard plus tests under .cupcake/), or an AGENTS.md / docs/agents entry where a guard is impossible, done directly or by a launched agent whose brief names those paths -- before any action on the instance. Reads may come first.",
	])
}

# CORRECTION|<sentence>. Anything else, including an empty signal, injects nothing.
sentence := s if {
	startswith(raw, "CORRECTION|")
	s := trim(substring(raw, count("CORRECTION|"), -1), " \t\r\n")
} else := ""

raw := trim(matched, " \t\r\n")

# Signal value tolerates both the bare-string and {output: ...} shapes cupcake may hand back.
matched := p if {
	p := input.signals.user_prompt_correction
	is_string(p)
} else := p if {
	p := input.signals.user_prompt_correction.output
	is_string(p)
} else := ""
