# METADATA
# scope: package
# title: After a reported guard miss, dispatch only guard work
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-TOOLS-BEFORE-PROBLEMS
#   description: >-
#     User directive 2026-10-18, verbatim: "I didn't say 'You don't have the correct course, here it
#     is, correct it.' I said: 'You don't have the correct course, its because your tools aren't
#     working, I don't want you to fix problems before you fix tools'".
#
#     The user reported "Multiple rego violations uncaught in this response". The main session sent
#     one subagent to widen the guards, which was the request, and a second to fix the build-kit
#     defects the uncaught message had named, which was not.
#
#     While the user's own last typed message reports a guard or tool miss (the `tool_miss_report`
#     signal, `scripts/cupcake_tool_miss_report.py`), an Agent/Task dispatch is refused unless its
#     prompt or description is about the guards: cupcake, rego, OPA, a policy, a signal, a stop hook,
#     a guard, a detector. The hold lasts for the turn that answers that message; the user's next
#     message lifts it, which is when the tool fix has been reported to them.
#
#     Scope, stated so its silence is not read as more than it is. Only dispatches are judged: a
#     direct Edit to product code in the same turn is not refused here, because the guard work this
#     rule protects is itself made of edits and telling them apart by path would refuse the fix
#     along with the digression. The on-topic test is a vocabulary test, so a product prompt that
#     happens to mention a guard (this repo has `profile_table_guard`) passes; that errs toward
#     allowing, which is the direction a misread should go for a rule that blocks work.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Agent", "Task"]
#     required_signals: ["tool_miss_report"]
package cupcake.policies.claude.no_product_work_before_tool_fix

import rego.v1

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name in {"Agent", "Task"}
	reported
	not on_tool_work
	decision := {
		"rule_id": "ER-EFFECTS-TOOLS-BEFORE-PROBLEMS",
		"reason": concat("", [
			"The user's last message reports that a guard or tool missed something ('",
			field("prompt"),
			"'). Fix the tool first: this dispatch is not about the guards, so it is product work started before the tool fix. The user's words: \"I don't want you to fix problems before you fix tools\". Dispatch only work on .cupcake policies, signals and their tests now, report the tool fix, and take up the product problem after the user's next message.",
		]),
		"severity": "HIGH",
	}
}

facts := line if {
	line := input.signals.tool_miss_report
	is_string(line)
	startswith(line, "TOOLMISS|")
}

field(name) := value if {
	some part in split(facts, "|")
	startswith(part, concat("", [name, "="]))
	value := substring(part, count(name) + 1, -1)
}

reported if field("reported") == "1"

dispatch_text := lower(concat(" ", [
	object.get(input.tool_input, "prompt", ""),
	object.get(input.tool_input, "description", ""),
]))

on_tool_work if {
	regex.match(
		`\.cupcake|cupcake|\brego\b|\bopa\b|stop[- ]hooks?|\bguards?\b|\bpolic(y|ies)\b|\bsignals?\b|\bdetectors?\b|pretooluse|userpromptsubmit|test-cupcake`,
		dispatch_text,
	)
}
