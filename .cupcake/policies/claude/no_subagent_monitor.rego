# METADATA
# scope: package
# title: A subagent waits with one background wait, never a Monitor
# authors: ["er-mods-rs agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-SUBAGENT-MONITOR
#   description: >-
#     Hard block on a `Monitor` call made by a subagent (a PreToolUse event that
#     carries `agent_id`). Every Monitor event wakes the subagent, and every time a
#     woken subagent ends its turn the main session receives a task-notification
#     ("is waiting on its own background work") that it has to answer. On
#     2026-11-14 the agent "Build v12 run tree with speed-up" watched a long
#     equality run through a Monitor that printed one line per finished set; the
#     main session got more than a dozen notifications in under an hour, each one a
#     reply the user had to scroll past, and the user's words were "You are getting
#     absolutely spammed by an agent". A subagent waiting on its own long work runs
#     ONE `run_in_background` Bash command whose exit is the event it needs (an
#     until-loop on the run's done marker or its pid), so the main session hears
#     from it once, when that work is over. The main session keeps Monitor, under
#     monitor_rate_limit.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Monitor"]
#     required_signals: ["subagent_id"]
package cupcake.policies.claude.no_subagent_monitor

import rego.v1

# Claude Code sets `agent_id` only on events a subagent raises. cupcake 0.5.2 hands it to a
# signal's stdin but not to `input` (measured 2026-11-14), so `.cupcake/signals/subagent_id.sh`
# prints it.
signal_value := v if {
	v := input.signals.subagent_id
	is_string(v)
} else := v if {
	v := input.signals.subagent_id.output
	is_string(v)
} else := ""

subagent if {
	trim(signal_value, " \r\n") != ""
}

block_reason := "🧁 Cupcake blocked a Monitor in a subagent. Every Monitor event wakes you, and every time you end a turn after one the main session gets a task-notification it must answer -- on 2026-11-14 one equality run sent the main session more than a dozen of them in under an hour, and the user called it spam. Wait on your own long work with ONE Bash command run with run_in_background whose exit is the event you need, for example\n\n    until [ -e <done marker> ] || ! kill -0 <pid> 2>/dev/null; do sleep 30; done; tail -n 40 <log>\n\nand read the log when it exits. Report interim progress only in your final result."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Monitor"
	subagent

	decision := {
		"rule_id": "ER-EFFECTS-NO-SUBAGENT-MONITOR",
		"severity": "HIGH",
		"reason": block_reason,
	}
}
