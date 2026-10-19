# METADATA
# scope: package
# title: Ban ending a turn on a promise nothing is going to keep
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-UNEXECUTED-PROMISE
#   description: >-
#     User directive 2026-08-22, in their words: "How can I prevent you from ever saying 'I'll
#     <statement of future action>' and then landing on no shells running, no monitors, and no
#     directive to me to explain why a user is required to re-initiate the task."
#
#     The instance: a turn ended "I'll re-record the directive with the shell metacharacters escaped
#     rather than leave it unsaved" -- and then stopped. No tool call, no background task, no
#     statement that the user had to do anything. The work simply evaporated and the user had to
#     notice and re-ask. A promise made in prose is enforceable by nothing; only a Stop hook that
#     refuses the stop closes this.
#
#     THE VIOLATION IS A CONJUNCTION OF FOUR FACTS, computed in the signal:
#       1. the turn's FINAL prose block commits the agent to a CONCRETE action ("I'll re-run the
#          gate", "I'm going to patch it", "let me check the offsets", and the bare present
#          continuous "I'm closing it" -- the shape that walked through this guard on 2026-09-01,
#          because present continuous reads as already underway and is therefore the more seductive
#          way to make a promise nothing keeps);
#       2. no tool_use follows that prose in the turn -- nothing executed it;
#       3. no background work is live -- no backgrounded Bash still awaiting its result, no async
#          subagent that has not notified, no detached shell / Monitor / SendMessage in the turn;
#       4. the message does not hand the obligation to the user -- no question, no blocker
#          statement, no "once you've X", no "I'll need you to Y", no "next session".
#     Any one of the four missing means the turn is fine, and the signal stays silent.
#
#     BIASED HARD TOWARD NOT FIRING. A guard that cries wolf gets ignored, which is worse than no
#     guard, so: only the closing prose block is scanned (a mid-turn "I'll check X" followed by the
#     tool call that checks X is the correct shape and is never touched); the committed verb must be
#     on a concrete-action allowlist, which excludes stance verbs ("I'll keep that in mind"), verbs
#     the message itself fulfils ("I'll summarise"), hedges ("I'll try to") and negations ("I'll
#     never"); and quoted/backticked/fenced spans are stripped so quoting the ban cannot trip it.
#
#     The signal emits PROMISE:<clause> with the offending sentence, which the halt quotes back so
#     the agent knows exactly which sentence it has to make true or withdraw.
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_unexecuted_promise"]
package cupcake.policies.claude.no_unexecuted_promise

import rego.v1

# Enforcement: block turn-end when the just-emitted turn closed on a promise nothing will keep.
halt contains decision if {
	input.hook_event_name == "Stop"
	some p in [phrase]
	decision := {
		"rule_id": "ER-EFFECTS-NO-UNEXECUTED-PROMISE",
		"reason": reason_for(p),
		"severity": "HIGH",
	}
}

reason_for(p) := msg if {
	kind == "BURIED"
	msg := concat("", [
		"Your closing message, as the user reads it: ", skeleton(p),
		" The user reads from the first word and stops when it stops being worth it, so the one sentence they have to act on came after ",
		format_int(counts.before, 10),
		" characters of explanation they did not need. A phrase that hands them the next step was there, which is the only reason this is not the plain unkept-promise halt -- but it does not help if the reader never gets to it, and adding a phrase like that in front of the same paragraph is how this shape got through before. Fix the shape, not the wording: make that sentence the FIRST sentence of the message, cut the prose around it to at most one short line of why, and drop any 'and I'll ...' tail if the user's reply is what starts that work anyway.",
	])
}

reason_for(p) := msg if {
	kind == "PROMISE"
	msg := concat("", [
		"You ended the turn on a promise nothing is going to keep. Your closing message, as the user reads it: ", skeleton(p),
		" No tool call executed it, no background task or shell is carrying it, and you did not tell the user the ball is theirs -- so that work evaporates and they have to notice and re-ask. Pick one, now, before stopping: (a) EXECUTE IT in this turn with the tool call that does it; (b) START IT as a background task (a backgrounded Bash or a subagent) so something real is carrying it; or (c) REWRITE it so the user knows they must re-initiate it: make that ask the FIRST sentence of the message and name the blocker -- an ask after a paragraph of explanation halts as BURIED. Deleting the promise and staying silent is not an option: if the work matters, say who does it next.",
	])
}

# `<N characters of prose> '<sentence>' <M characters of prose>`, with an empty side left out. This is
# the user's own rendering of the failure (2026-10-19): the message reduced to the one sentence that
# mattered and the size of what surrounded it.
skeleton(p) := concat("", [side(counts.before, "<", "> "), "'", p, "'", side(counts.after, " <", ">")])

side(n, open, close) := "" if n == 0

side(n, open, close) := concat("", [open, format_int(n, 10), " characters of prose", close]) if n > 0

kind := "BURIED" if startswith(raw, "BURIED:")

else := "PROMISE"

# The signal writes `<KIND>:<before>:<after>:<sentence>`. A bare `PROMISE:<sentence>` (an older
# signal, or a crafted one) still halts, with both counts at zero.
body := trim_prefix(trim_prefix(raw, "BURIED:"), "PROMISE:")

parts := regex.find_all_string_submatch_n(`^(\d+):(\d+):([\s\S]*)$`, body, 1)

counts := {"before": to_number(parts[0][1]), "after": to_number(parts[0][2])} if {
	count(parts) == 1
} else := {"before": 0, "after": 0}

# Parse the tagged signal into the offending clause. Untagged-but-non-empty falls back to the raw
# value so a bare/crafted signal still halts. Empty -> phrase undefined -> no halt.
phrase := p if {
	count(parts) == 1
	p := trim(parts[0][3], " \t\r\n")
} else := p if {
	raw != ""
	p := trim(body, " \t\r\n")
}

raw := trim(matched_phrase, " \t\r\n")

# Signal value tolerates both the bare-string and {output: ...} shapes cupcake may hand back.
matched_phrase := p if {
	p := input.signals.last_assistant_unexecuted_promise
	is_string(p)
} else := p if {
	p := input.signals.last_assistant_unexecuted_promise.output
} else := ""
