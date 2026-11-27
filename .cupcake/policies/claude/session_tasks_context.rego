# METADATA
# scope: package
# title: Put this session's running shells and agents in front of every turn
# authors: ["er-quickload agents"]
# custom:
#   severity: LOW
#   id: ER-EFFECTS-SESSION-TASKS-CONTEXT
#   description: >-
#     User words 2026-11-11, verbatim: "I refuse to tell you, and encourge you to find a way to self
#     discover what shells you kick off. I didn't run any shells" and "So next time, how are you
#     going to monitor your own shells and understand when they are running so you can communicate
#     clearly to the user why you're paused (blocked because X shell is running) or whatever you're
#     doing". The user's view showed shells and agents the main session could not account for: they
#     were a subagent's, whose shells never reach the main transcript. On UserPromptSubmit, while
#     any shell or agent of this Claude Code session runs, inject what
#     scripts/cupcake_session_tasks.py measures from the process table (signal session_tasks): each
#     shell's task id, its owner (the main session, or an agent id and description), what it is
#     doing and how long it has run, and each agent still working. Nothing is injected when nothing
#     runs. The Stop half is idle_hold.rego (ER-EFFECTS-PAUSE-NAMES-BLOCKER), which halts a paused
#     closing message that names none of it.
#   routing:
#     required_events: ["UserPromptSubmit"]
#     required_signals: ["session_tasks"]
package cupcake.policies.claude.session_tasks_context

import rego.v1

add_context contains context if {
	input.hook_event_name == "UserPromptSubmit"
	report != ""
	context := concat("", [
		"RUNNING NOW in this session, measured from the process table by python3 scripts/cupcake_session_tasks.py (not inferred from the transcript): ",
		report,
		" A closing message that pauses names the shell's task id or the agent it is blocked on.",
	])
}

report := trim(matched, " \t\r\n")

# Signal value tolerates both the bare-string and {output: ...} shapes cupcake may hand back.
matched := p if {
	p := input.signals.session_tasks
	is_string(p)
} else := p if {
	p := input.signals.session_tasks.output
	is_string(p)
} else := ""
