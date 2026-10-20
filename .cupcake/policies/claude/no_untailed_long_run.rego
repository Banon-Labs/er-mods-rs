# METADATA
# scope: package
# title: Ban ending a turn that started a long background run without a kitty tab tailing it
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-UNTAILED-LONG-RUN
#   description: >-
#     User request 2026-10-19: "When we regenerate the board, is there a skill we can make and an
#     agent instruction or rego guard to remind you to open a kitty tab tailing the real output
#     from the run?" A board regeneration of half an hour or more was backgrounded with no tab,
#     although AGENTS.md and the tail-build-task skill already said to open one.
#
#     The signal reads the turn being stopped for a Bash call that went to the background and
#     runs a long run from its LONG_RUNS list (board regeneration, weapon and DLL builds, the
#     check suite, git push, ...), with no later kitty-tail-task.py or kitty-tail-push.py call in
#     the same turn that follows the run's real output. The halt names each such command and the
#     tail command to run for it.
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_untailed_long_run"]
package cupcake.policies.claude.no_untailed_long_run

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	some r in [runs]
	decision := {
		"rule_id": "ER-EFFECTS-NO-UNTAILED-LONG-RUN",
		"reason": reason_for(r),
		"severity": "HIGH",
	}
}

reason_for(r) := msg if {
	msg := concat("", [
		"This turn started a long run in the background and opened no kitty tab tailing its real output: ", r,
		". Run the tail command named after each one now, in this turn (it opens with --keep-focus and closes itself 5 s after the run ends; see the tail-build-task skill). ",
		"If the tab command fails, say so in the closing message with its error.",
	])
}

runs := s if {
	startswith(raw, "UNTAILEDRUN:")
	s := trim(trim_prefix(raw, "UNTAILEDRUN:"), " \t\r\n")
	s != ""
}

raw := trim(signal_value, " \t\r\n")

signal_value := v if {
	v := input.signals.last_assistant_untailed_long_run
	is_string(v)
} else := v if {
	v := input.signals.last_assistant_untailed_long_run.output
	is_string(v)
} else := ""
