# METADATA
# scope: package
# title: Ban ending a turn with background tasks still running and unaccounted for
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-UNEXPLAINED-RUNNING-TASKS
#   description: >-
#     User directive 2026-10-07: "any time you have a monitor that runs after you don't need it,
#     the first thing you should do is stop it". A stage-log copier loop and an until-loop waiter
#     were left running in the background long after the push they served had finished, and were
#     stopped only when the user complained.
#
#     The signal reads the transcript for every Bash or Monitor task the session started in the
#     background that has neither reported a finish (task-notification, Monitor expiry, task_status)
#     nor been stopped (TaskStop/KillShell), and fires when the closing message does not name each
#     such task id in a sentence that says why it is still needed. The halt lists the ids.
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_unexplained_running_tasks"]
package cupcake.policies.claude.no_unexplained_running_tasks

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	some t in [tasks]
	decision := {
		"rule_id": "ER-EFFECTS-NO-UNEXPLAINED-RUNNING-TASKS",
		"reason": reason_for(t),
		"severity": "HIGH",
	}
}

reason_for(t) := msg if {
	msg := concat("", [
		"Background tasks you started are still running and the closing message does not account for them: ", t,
		". Any monitor or loop you no longer need, stop it first (TaskStop with its task id) -- that is the first thing to do, before the report. ",
		"For one that is still needed, name its task id in the closing message in a sentence that says what it is still waiting for or doing.",
	])
}

tasks := s if {
	startswith(raw, "UNEXPLAINEDTASKS:")
	s := trim(trim_prefix(raw, "UNEXPLAINEDTASKS:"), " \t\r\n")
	s != ""
}

raw := trim(signal_value, " \t\r\n")

signal_value := v if {
	v := input.signals.last_assistant_unexplained_running_tasks
	is_string(v)
} else := v if {
	v := input.signals.last_assistant_unexplained_running_tasks.output
	is_string(v)
} else := ""
