# METADATA
# scope: package
# title: A forecast of time or effort cites a measured number, or is not made
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-UNMEASURED-ESTIMATE
#   description: >-
#     User correction 2026-11-11: "You also estimated yesterday over 24 hours ago after I started
#     this project that generating similiarity scores woudln't be a big lift", then "Should an LLM be
#     prohibited from making non-cited claims about timelines?". On 2026-11-09 the agent called the
#     similarity matrix "nearly free ... a few seconds on top of a kit run" and "roughly 20 to 30
#     minutes" with nothing timed; the measured full runs took 6,863 s, 9,827 s and 12,716 s. Halts a
#     closing message that sizes development work or puts a size word where a number would go
#     (kind=effort, halted outright), or forecasts a run's duration with no measured timing in the
#     same message (kind=runtime). Classifier: scripts/cupcake_unmeasured_estimate.py (--selftest,
#     --replay). Described in docs/agents/runtime-evidence.md, "Forecasts of time and effort are claims,
#     cited or not made".
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_unmeasured_estimate"]
package cupcake.policies.claude.no_unmeasured_estimate

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	raw != ""
	decision := {
		"rule_id": "ER-EFFECTS-NO-UNMEASURED-ESTIMATE",
		"reason": concat("", [
			object.get(lead, kind, lead.runtime),
			"'",
			forecast,
			"'. ",
			object.get(fix, kind, fix.runtime),
			" The reasoning and the record behind this guard: docs/agents/runtime-evidence.md, section 'Forecasts of time and effort are claims, cited or not made'.",
		]),
		"severity": "HIGH",
	}
}

lead := {
	"effort": "Your closing message sizes development work, or puts a size word where a number would go: ",
	"runtime": "Your closing message forecasts how long a run takes or has left and states no measured number it projects from: ",
}

fix := {
	"effort": "Development effort has no measured number before the work exists, and a size word ('nearly free', 'not a big lift', 'quick', 'easy', 'a small change') cites none, so no number elsewhere in the message clears it. Send the message again without the forecast: say the effort is unmeasured, and if its size matters, time the smallest sample that would measure it and report that number instead.",
	"runtime": "Time a sample first, or read the running job's pace (items done and the time they took) or its timing log, and put that measured number beside the forecast. If you have not measured it, say so and give no number.",
}

# --- signal parsing ------------------------------------------------------------------------------
# UNMEASURED|kind=..|forecast=..
# The leading tag carries no "=" so it drops out of the fact map on its own.
fact[k] := v if {
	some kv in split(raw, "|")
	n := indexof(kv, "=")
	n > 0
	k := trim(substring(kv, 0, n), " \t\r\n")
	v := trim(substring(kv, n + 1, -1), " \t\r\n")
}

kind := object.get(fact, "kind", "runtime")

forecast := object.get(fact, "forecast", "")

raw := trim(matched_facts, " \t\r\n")

# Signal value tolerates both the bare-string and {output: ...} shapes cupcake may hand back.
matched_facts := p if {
	p := input.signals.last_assistant_unmeasured_estimate
	is_string(p)
} else := p if {
	p := input.signals.last_assistant_unmeasured_estimate.output
	is_string(p)
} else := ""
