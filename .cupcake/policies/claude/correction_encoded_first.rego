# METADATA
# scope: package
# title: Encode a user's correction for future agents before acting on the instance
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-CORRECTION-ENCODED-FIRST
#   description: >-
#     User directive 2026-11-11, verbatim: "My largest concern is that every time I correct you, you
#     fix the issue instead of fix it for future agents first".
#
#     Measured in session 5ec7b779. The user typed "If I ever see a subagent that is finished but
#     hasn't been closed, I always have VERY HIGH concerns that you consumed its results. I'm VERY
#     WORRIED that you missed the changes UNTIL you close it". The turn ran ListAgents, two read-only
#     Bash checks, a TaskStop on one agent and a SendMessage telling a running subagent to wrap up,
#     then closed on prose. It changed no guard, test or instruction file and launched no agent to
#     change one. Only after the next message ("I was not concerned about you closing that specific
#     agent whatsoever") did it launch an agent to build the Stop guard.
#
#     Halts a turn opened by a prompt that reads as a correction when an action on the instance came
#     before any action that encodes the correction for future agents, or when the turn ended with
#     no such action at all and its closing message gives no reason enforcement is impossible.
#     Encoding is an Edit/Write to .cupcake/**, AGENTS.md, docs/agents/**, scripts/cupcake_*.py,
#     scripts/test-cupcake-*.py or scripts/test-*-signal.py, or an agent launch whose brief names
#     one of those paths (a bare "guard" or "policy" with no path does not count). Reads do not
#     count either way. Once this guard has halted
#     a turn and the turn has encoded the correction, it may end: the order cannot be undone.
#     The classifier, the split and the replay of the measured turns are in
#     scripts/cupcake_correction_first.py (--selftest), scripts/test-correction-first-signal.py and
#     scripts/test-cupcake-correction-classifier.py. Its UserPromptSubmit half is
#     correction_encoded_first_reminder.rego.
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_correction_encoded_first"]
package cupcake.policies.claude.correction_encoded_first

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	raw != ""
	decision := {
		"rule_id": "ER-EFFECTS-CORRECTION-ENCODED-FIRST",
		"reason": reason,
		"severity": "HIGH",
	}
}

# The reason opens with "Correction not encoded first": scripts/cupcake_correction_first.py finds
# this guard's own Stop feedback in the transcript by that phrase, so keep the two in step.
reason := concat("", [
	"Correction not encoded first: the user's message reads as a correction ('",
	object.get(fact, "correction", ""),
	"'). ",
	what_happened,
	" User directive 2026-11-11: \"My largest concern is that every time I correct you, you fix the issue instead of fix it for future agents first\". Encode it for future agents first: a guard plus tests under .cupcake/ (or an AGENTS.md / docs/agents entry where a guard is impossible), made directly or by an agent whose brief names those paths. If neither a guard nor an entry can carry it, say why in one sentence of the closing message ('this cannot be enforced because ...').",
])

what_happened := msg if {
	object.get(fact, "verdict", "") == "instance_first"
	object.get(fact, "encoded_later", "0") == "1"
	msg := concat("", [
		"You acted on the instance (",
		object.get(fact, "first_instance", ""),
		") before you encoded it. The encoding exists now, so the turn may end after this halt; the next correction gets encoded before anything else.",
	])
} else := msg if {
	object.get(fact, "verdict", "") == "instance_first"
	msg := concat("", [
		"You acted on the instance (",
		object.get(fact, "first_instance", ""),
		") and encoded nothing for future agents.",
	])
} else := "The turn ended without encoding it: no edit under .cupcake/, AGENTS.md or docs/agents/, and no agent launched to make one."

# --- signal parsing ------------------------------------------------------------------------------
# CORRECTIONFACTS|correction=..|verdict=..|first_instance=..|encoded_later=..
fact[k] := v if {
	some kv in split(raw, "|")
	n := indexof(kv, "=")
	n > 0
	k := trim(substring(kv, 0, n), " \t\r\n")
	v := trim(substring(kv, n + 1, -1), " \t\r\n")
}

raw := trim(matched_facts, " \t\r\n")

# Signal value tolerates both the bare-string and {output: ...} shapes cupcake may hand back.
matched_facts := p if {
	p := input.signals.last_assistant_correction_encoded_first
	is_string(p)
} else := p if {
	p := input.signals.last_assistant_correction_encoded_first.output
	is_string(p)
} else := ""
