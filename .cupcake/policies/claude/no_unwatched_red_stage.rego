# METADATA
# scope: package
# title: Ban ending a turn while a background check suite runs with no Monitor that wakes on a red stage
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-UNWATCHED-RED-STAGE
#   description: >-
#     User words 2026-11-25: "It would be wise, and prudent, to not wait until all checks pass
#     before addressing any one of the failing reds in each future case >>> stage policy RED
#     (exit 1) 1702s .../policy.log". A backgrounded `git push` ran the pre-push suite with a kitty
#     split tailing it and nothing armed to wake the agent, so the policy stage's red waited for
#     the push's completion notification, after every other stage had finished.
#
#     The signal (scripts/cupcake_red_stage_watch.py) reports each still-running background
#     `git push`, hand-run pre-push hook or full check.sh with no still-running Monitor whose
#     command follows that task's output file and passes `RED`. A Monitor expires after 30 minutes,
#     so a suite that outlives it is reported again. The halt names the Monitor command to arm.
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_unwatched_red_stage"]
package cupcake.policies.claude.no_unwatched_red_stage

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	some s in [suites]
	decision := {
		"rule_id": "ER-EFFECTS-NO-UNWATCHED-RED-STAGE",
		"reason": reason_for(s),
		"severity": "HIGH",
	}
}

reason_for(s) := msg if {
	msg := concat("", [
		"A check suite is running in the background and nothing will wake you when one of its stages goes red: ", s,
		". Arm the Monitor named after each one now (or the --pid form scripts/kitty-tail-push.py printed, which ends with the push). ",
		"When it reports a `>>> stage <name> RED` line, read that stage's log and start the fix at once; do not wait for the other stages. ",
		"Re-arm it when it expires while the suite still runs. Where an entry says a pipe holds the stage lines until the task ends, no Monitor can wake on it: do what that entry says. ",
		"If the task has already ended, TaskStop its id.",
	])
}

suites := s if {
	startswith(raw, "UNWATCHEDRED:")
	s := trim(trim_prefix(raw, "UNWATCHEDRED:"), " \t\r\n")
	s != ""
}

raw := trim(signal_value, " \t\r\n")

signal_value := v if {
	v := input.signals.last_assistant_unwatched_red_stage
	is_string(v)
} else := v if {
	v := input.signals.last_assistant_unwatched_red_stage.output
	is_string(v)
} else := ""
