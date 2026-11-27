# METADATA
# scope: package
# title: Ban ending a turn while a subagent that finished is still open
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-UNCLOSED-FINISHED-AGENTS
#   description: >-
#     User directive 2026-11-11, verbatim: "If I ever see a subagent that is finished but hasn't
#     been closed, I always have VERY HIGH concerns that you consumed its results. I'm VERY WORRIED
#     that you missed the changes UNTIL you close it".
#
#     The signal reads the transcript for every subagent the main session launched whose latest
#     task-notification says it finished (not "is waiting on its own background work") and that no
#     TaskStop has named since. A SendMessage that resumed it makes it running again until it next
#     notifies. Agents launched by subagents are not counted. The halt names each open agent's id
#     and description; nothing in the closing message excuses one.
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_unclosed_finished_agents"]
package cupcake.policies.claude.no_unclosed_finished_agents

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	some a in [agents]
	decision := {
		"rule_id": "ER-EFFECTS-NO-UNCLOSED-FINISHED-AGENTS",
		"reason": reason_for(a),
		"severity": "HIGH",
	}
}

reason_for(a) := msg if {
	msg := concat("", [
		"Subagents you launched have finished and you have not closed them: ", a,
		". The user, 2026-11-11: \"If I ever see a subagent that is finished but hasn't been closed, I always have VERY HIGH concerns that you consumed its results.\" ",
		"For each one, consume its result first -- read its final report and the changes it made -- and then close it with TaskStop, task_id set to its agent id. ",
		"On an agent that already completed, TaskStop answers that it is not running or that no task has that id; that call is still the close. ",
		"An agent that is waiting on its own background work is not closed until it finishes: a TaskStop on it kills that work, and no_stop_waiting_agent refuses one.",
	])
}

agents := s if {
	startswith(raw, "UNCLOSEDAGENTS:")
	s := trim(trim_prefix(raw, "UNCLOSEDAGENTS:"), " \t\r\n")
	s != ""
}

raw := trim(signal_value, " \t\r\n")

signal_value := v if {
	v := input.signals.last_assistant_unclosed_finished_agents
	is_string(v)
} else := v if {
	v := input.signals.last_assistant_unclosed_finished_agents.output
	is_string(v)
} else := ""
