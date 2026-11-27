# METADATA
# scope: package
# title: A fix the agent files is a fix the agent starts, unless something blocks it
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-FILED-NOT-STARTED
#   description: >-
#     User directive 2026-11-10: "The only time I would want you to mention a fix and not work on
#     it is if you're blocked when it relates to what we are working on already, and this needs to
#     be internalized for all future sessions." The turn before had filed a missing mechanic as a
#     bd issue and closed on "It waits for the homing and requirement-line changes", which blocked
#     nothing: a separate worktree took it the next turn. Halts a turn that ran `bd create` and
#     started nothing after it (no Agent, Workflow, Edit or Write) while its closing message names
#     no blocker (scripts/cupcake_filed_not_started.py, --selftest). Since 2026-11-20 it also halts
#     a closing message that parks a written fix behind a running job ("I'll apply it when the kit
#     run (b3fgttzcf) ends"): the job has already loaded its code, so it blocks nothing. User,
#     2026-11-20: "yes, your quoted message and my last message are consistent, but inconsistent
#     with yoru actions".
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_filed_not_started"]
package cupcake.policies.claude.no_filed_not_started

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	raw != ""
	object.get(fact, "kind", "filed") == "filed"
	decision := {
		"rule_id": "ER-EFFECTS-NO-FILED-NOT-STARTED",
		"reason": concat("", [
			"You filed work this turn (bd create, ",
			object.get(fact, "filed", "1"),
			" issue(s)) and started none of it, and your closing message names no blocker ('",
			object.get(fact, "closing", ""),
			"'). The user's rule (2026-11-10): mention a fix without working on it only when you are blocked. Start it now -- a background agent in its own worktree when it touches files other work is changing -- or, if something really blocks it, name that blocker in the closing message: a credential, sudo, a live game, a guard that refused, a tool that failed to connect, or the running task it depends on ('blocked on task <id>').",
		]),
		"severity": "HIGH",
	}
}

halt contains decision if {
	input.hook_event_name == "Stop"
	raw != ""
	object.get(fact, "kind", "filed") == "deferred"
	decision := {
		"rule_id": "ER-EFFECTS-NO-FILED-NOT-STARTED",
		"reason": concat("", [
			"Your closing message parks a fix behind a running job ('",
			object.get(fact, "closing", ""),
			"'). A running job is not a blocker: it has already loaded its code, so editing the files changes it only if it restarts, and the next run recomputes anyway. The user's rule (2026-11-10, restated 2026-11-20): mention a fix without working on it only when you are blocked. Apply the fix now and say it is applied, or, if something really blocks it, name that blocker: a credential, sudo, a live game, a guard that refused, a tool that failed to connect, or a fix made from that run's output.",
		]),
		"severity": "HIGH",
	}
}

# --- signal parsing ------------------------------------------------------------------------------
# FILEDFACTS|kind=filed|filed=..|closing=..   or   FILEDFACTS|kind=deferred|closing=..
# A line with no kind is a filing (the shape before 2026-11-20).
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
	p := input.signals.last_assistant_filed_not_started
	is_string(p)
} else := p if {
	p := input.signals.last_assistant_filed_not_started.output
	is_string(p)
} else := ""
