# METADATA
# scope: package
# title: Answer the question asked, not a non-event nobody raised
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-NEGATED-OPENER
#   description: >-
#     User correction 2026-11-10. Asked "What does this mean to me?", the agent opened with
#     "Nothing in the game changed." -- a thing that did not happen and that nobody had asked
#     about -- and the answer only started in the second sentence. The user: "If I shouldn't do
#     it, why do you do it, and I have to remind you not to?". The every-turn reminder to lead
#     with the answer was in context and did not stop it, so this halts the turn instead. The
#     signal fires only when the prompt holds a what/how/why/which question that is not about
#     change or absence and the closing message's first sentence starts with Nothing or None
#     (scripts/cupcake_negated_opener.py, --selftest).
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_negated_opener"]
package cupcake.policies.claude.no_negated_opener

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	raw != ""
	decision := {
		"rule_id": "ER-EFFECTS-NO-NEGATED-OPENER",
		"reason": concat("", [
			"The user asked '",
			question,
			"' and your reply opens with something that did not happen: '",
			opener,
			"'. They did not ask about that, so it is not the answer, and they have to read past it to find one. Send the reply again with a first sentence that answers their question about their own situation. Drop the non-event, or move it after the answer if it is load-bearing.",
		]),
		"severity": "HIGH",
	}
}

# --- signal parsing ------------------------------------------------------------------------------
# NEGOPENER|question=..|opener=..
# The leading tag carries no "=" so it drops out of the fact map on its own.
fact[k] := v if {
	some kv in split(raw, "|")
	n := indexof(kv, "=")
	n > 0
	k := trim(substring(kv, 0, n), " \t\r\n")
	v := trim(substring(kv, n + 1, -1), " \t\r\n")
}

question := object.get(fact, "question", "")

opener := object.get(fact, "opener", "")

raw := trim(matched_facts, " \t\r\n")

# Signal value tolerates both the bare-string and {output: ...} shapes cupcake may hand back.
matched_facts := p if {
	p := input.signals.last_assistant_negated_opener
	is_string(p)
} else := p if {
	p := input.signals.last_assistant_negated_opener.output
	is_string(p)
} else := ""
