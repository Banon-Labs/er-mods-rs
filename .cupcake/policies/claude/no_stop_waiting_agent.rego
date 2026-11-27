# METADATA
# scope: package
# title: Refuse a TaskStop on a subagent still waiting on its own background work
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-STOP-WAITING-AGENT
#   description: >-
#     2026-11-11, session 5ec7b779: subagent abce87a3bda959d98 notified that it "is waiting on its
#     own background work"; its background selftest b7rzimvi4 was still running. The main session
#     called TaskStop on the agent to close it, as the Stop guard no_unclosed_finished_agents tells
#     it to do for finished agents, and the selftest died with it: its output ends in "[killed]".
#
#     A waiting agent is not finished. It notifies again, "finished", when its own background work
#     ends, and that is when it is closed. The `task_stop_waiting_agent` signal
#     (`scripts/cupcake_task_stop_waiting_agent.py`) reads the agent's state with
#     `cupcake_turn_scan.subagent_states`, the reader the Stop guard uses, and this policy denies the
#     TaskStop while that state is "waiting". A bash or monitor task id, and an agent that finished,
#     was stopped or is running, are not judged.
#
#     The override is the user's own last typed message naming the agent (its id or a 7-character
#     prefix of it) or asking to stop it, read the way no_product_work_before_tool_fix reads it:
#     only a message the user typed counts.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["TaskStop"]
#     required_signals: ["task_stop_waiting_agent"]
package cupcake.policies.claude.no_stop_waiting_agent

import rego.v1

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "TaskStop"
	waiting
	not overridden
	decision := {
		"rule_id": "ER-EFFECTS-NO-STOP-WAITING-AGENT",
		"reason": concat("", [
			"Agent ", field_or_empty("id"), " (\"", field_or_empty("description"), "\") is not finished: its latest notification says it is waiting on its own background work. ",
			"A TaskStop on it kills that work with it -- on 2026-11-11 a TaskStop on a waiting agent killed its running selftest. ",
			"Leave it running. It will notify \"finished\" when that work ends; consume its result then and close it with TaskStop. ",
			"Stop it now only if the user asks: their own message naming the agent id or asking to stop the agent lets this through.",
		]),
		"severity": "HIGH",
	}
}

waiting if field_or_empty("id") != ""

overridden if field_or_empty("override") == "1"

facts := line if {
	line := signal_value
	startswith(line, "WAITINGAGENT|")
} else := ""

field_or_empty(name) := value if {
	some part in split(facts, "|")
	startswith(part, concat("", [name, "="]))
	value := trim(substring(part, count(name) + 1, -1), " \t\r\n")
} else := ""

signal_value := v if {
	v := input.signals.task_stop_waiting_agent
	is_string(v)
} else := v if {
	v := input.signals.task_stop_waiting_agent.output
	is_string(v)
} else := ""
