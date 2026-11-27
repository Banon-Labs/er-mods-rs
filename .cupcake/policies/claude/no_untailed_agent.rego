# METADATA
# scope: package
# title: Ban ending a turn that started a background subagent without a kitty tab following it
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-UNTAILED-AGENT
#   description: >-
#     User, 2026-11-11: "I don't understand how you could have a round running without me being
#     able to see the output". The main session had launched a background Agent ("Speed up attack
#     sets, round 2") and opened no view of it, while long background runs already had to be
#     tailed (no_untailed_long_run).
#
#     The signal reads the turn being stopped (back to the last prompt that is not a Stop hook's
#     feedback) for an Agent call answered with a background launch naming its agentId, or a
#     SendMessage that resumed one, with no later scripts/kitty-tail-agent.py call naming that id,
#     while the agent is still working (scripts/cupcake_untailed_agent.py). The halt names each
#     such agent and the command to run with its id.
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_untailed_agent"]
package cupcake.policies.claude.no_untailed_agent

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	some a in [agents]
	decision := {
		"rule_id": "ER-EFFECTS-NO-UNTAILED-AGENT",
		"reason": reason_for(a),
		"severity": "HIGH",
	}
}

reason_for(a) := msg if {
	msg := concat("", [
		"This turn started a background subagent and opened no kitty tab following its transcript: ", a,
		". The user, 2026-11-11: \"I don't understand how you could have a round running without me being able to see the output\". ",
		"Run the command after the arrow now, in this turn: it opens one tab per agent with --keep-focus, follows ~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl and closes itself 5 s after the agent finishes. ",
		"If the tab command fails, say so in the closing message with its error.",
	])
}

agents := s if {
	startswith(raw, "UNTAILEDAGENT:")
	s := trim(trim_prefix(raw, "UNTAILEDAGENT:"), " \t\r\n")
	s != ""
}

raw := trim(signal_value, " \t\r\n")

signal_value := v if {
	v := input.signals.last_assistant_untailed_agent
	is_string(v)
} else := v if {
	v := input.signals.last_assistant_untailed_agent.output
	is_string(v)
} else := ""
