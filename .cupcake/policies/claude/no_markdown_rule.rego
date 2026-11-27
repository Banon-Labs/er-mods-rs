# METADATA
# scope: package
# title: Text in a markdown file is guidance, not a rule
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-MARKDOWN-RULE
#   description: >-
#     User correction 2026-11-11: "This is probably the 100th time I've told you that its logically
#     inconsistent to use the word rule in reference to markdown files". An instruction file, a
#     memory or a note is advisory: an agent can miss it or ignore it, and nothing stops the turn that
#     does. Only executable enforcement (a policy, a test, a hook) binds. The same session closed on
#     "The rule is now in docs/agents/runtime-evidence.md" and "AGENTS.md now carries the same rule".
#     Halts a closing message that calls a markdown file, a docs/ entry, the notes or the
#     instructions a rule; a rego, policy, lint or named guard's rule, a game rule and a quoted word
#     pass. Classifier: scripts/cupcake_markdown_rule.py (--selftest, --replay).
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_markdown_rule"]
package cupcake.policies.claude.no_markdown_rule

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	raw != ""
	decision := {
		"rule_id": "ER-EFFECTS-NO-MARKDOWN-RULE",
		"reason": concat("", [
			"Your closing message calls text in a markdown file (",
			referent,
			") a rule: '",
			sentence,
			"'. A markdown entry is guidance: a note, a convention, or a description of what a guard enforces. Nothing stops an agent that misses or ignores it, so it binds nothing; only executable enforcement (a policy, a test, a hook) does. The user, 2026-11-11: \"This is probably the 100th time I've told you that its logically inconsistent to use the word rule in reference to markdown files\". ",
			"Send the message again with that sentence reworded to say what the file does ('AGENTS.md describes it', 'the note in docs/agents/...', 'the convention in ...'), or name the guard that enforces it. If you mean the word itself, put it in quotes.",
		]),
		"severity": "HIGH",
	}
}

# --- signal parsing ------------------------------------------------------------------------------
# MDRULE|referent=..|sentence=..
# The leading tag carries no "=" so it drops out of the fact map on its own.
fact[k] := v if {
	some kv in split(raw, "|")
	n := indexof(kv, "=")
	n > 0
	k := trim(substring(kv, 0, n), " \t\r\n")
	v := trim(substring(kv, n + 1, -1), " \t\r\n")
}

referent := object.get(fact, "referent", "a markdown file")

sentence := object.get(fact, "sentence", "")

raw := trim(matched_facts, " \t\r\n")

# Signal value tolerates both the bare-string and {output: ...} shapes cupcake may hand back.
matched_facts := p if {
	p := input.signals.last_assistant_markdown_rule
	is_string(p)
} else := p if {
	p := input.signals.last_assistant_markdown_rule.output
	is_string(p)
} else := ""
