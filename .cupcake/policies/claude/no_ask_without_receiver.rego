# METADATA
# scope: package
# title: Ban asking the user for an in-game action with nothing armed to receive it
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-ASK-WITHOUT-RECEIVER
#   description: >-
#     User directive 2026-10-02: "When telling me you 'need' something, that you're already
#     prepared to receive the information. In this case, you set no monitors, so you needed
#     nothing from me. You only needed yourself to set a monitor."
#
#     The signal fires when the closing prose asks the user for an action or event ("I need you
#     to", "please <verb>", "let me know when", "once you <verb>", "waiting on you") and the
#     transcript shows no live receiver: no Monitor that started and has not ended, and no
#     backgrounded Bash whose own exit is the awaited event (an until-loop on a log, inotifywait,
#     grep -m). A background job that only runs -- a Frida watcher, a build -- does not count,
#     because its exit is not what the user is being asked to cause. Asks answered by the user's
#     typed reply (judgement, preference, approval, credential, sudo, paste) are exempt in the
#     signal. The halt quotes the offending sentence back.
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_ask_without_receiver"]
package cupcake.policies.claude.no_ask_without_receiver

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	some s in [sentence]
	decision := {
		"rule_id": "ER-EFFECTS-NO-ASK-WITHOUT-RECEIVER",
		"reason": reason_for(s),
		"severity": "HIGH",
	}
}

reason_for(s) := msg if {
	msg := concat("", [
		"You asked the user for something you have no receiver for: '", s,
		"'. Nothing armed in this session will wake you when they do it -- no live Monitor, no background wait whose exit is the event -- so the result would sit in a log until they come back and say 'done'. ",
		"Arm the receiver first: a Monitor on the instrument that captures the result (throttled, see monitor_rate_limit), or a run_in_background until-loop that exits when the event lands. Then ask. ",
		"If the answer is only their typed reply (a judgement, a decision), ask it as that question instead; if you need nothing from them, drop the ask.",
	])
}

sentence := s if {
	startswith(raw, "ASKNORECEIVER:")
	s := trim(trim_prefix(raw, "ASKNORECEIVER:"), " \t\r\n")
} else := s if {
	raw != ""
	s := raw
}

raw := trim(signal_value, " \t\r\n")

signal_value := v if {
	v := input.signals.last_assistant_ask_without_receiver
	is_string(v)
} else := v if {
	v := input.signals.last_assistant_ask_without_receiver.output
	is_string(v)
} else := ""
