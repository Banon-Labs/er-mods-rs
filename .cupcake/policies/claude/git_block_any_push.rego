# METADATA
# scope: package
# title: The Agent Does Not Push
# authors: ["er-quickload agents"]
# custom:
#   severity: CRITICAL
#   id: ER-EFFECTS-BLOCK-ANY-PUSH
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.git_block_any_push

import rego.v1

import data.cupcake.system.commands

# User directive 2026-09-17, verbatim: "I stopped the monitor, because quite
# frankly you should NEVER be pushing." Restated the same day as "No push. Push
# bad" after an agent pushed anyway, which is why this file exists: the directive
# had been recorded as a `bd` memory and a memory is advisory, so the next agent
# in the same session read a red CI hook and pushed to fix the remote.
#
# Committing stays allowed. Pushing does not, in any form: not a feature branch,
# not a tag, not a deletion, not through `scripts/er-push-watched.sh`, not inside
# a `Monitor`, and not because a CI-state hook reports a failing job. A CI-state
# report is context, never an instruction to update the remote.
#
# Two concrete costs, both measured on this machine. The pre-push hook runs the
# full check suite: ten minutes with every core pinned, three times in one
# session while the user was playing the game the agent had launched for them.
# And the remote is the user's to publish -- a branch that appears there without
# them asking has already left the machine by the time they see it.
#
# This guard is deliberately unconditional, unlike `git_block_main_push`, which
# carries four exceptions for worktrees, explicit branches, rename refspecs and
# deletions. None of those carry over. There is no push this agent may make, so
# there is no shape to parse and no exception to get wrong -- which also means
# this rule adds no new regex to the rulebook beyond the two it borrows, and a
# regex added here is a hazard to every policy at once (see bd
# `a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14`).
#
# If the user asks for a push in so many words, they can run it themselves or
# lift the guard; an agent that wants around it is the case this was written for.
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	any_executed_push

	decision := {
		"rule_id": "ER-EFFECTS-BLOCK-ANY-PUSH",
		"reason": "The agent does not push. Commit and leave the branch local, then say plainly which commits are unpushed. Pushing is the user's, including when a CI-state hook reports a failing job -- that is context, not an instruction to update the remote.",
		"severity": "CRITICAL",
	}
}

# The watched-push helper is the same act wearing a script name, and the user
# named it when they gave the directive. Matched as a plain substring because the
# script is invoked by path and there is nothing to parse.
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	contains(lower(input.tool_input.command), "er-push-watched")

	decision := {
		"rule_id": "ER-EFFECTS-BLOCK-ANY-PUSH",
		"reason": "`scripts/er-push-watched.sh` pushes, so the agent does not run it. Leave the commits local and say which ones are unpushed.",
		"severity": "CRITICAL",
	}
}

# Fail closed on a payload this guard cannot read. `bash -c $CMD` hands a shell a
# program that is not in the command text, so nothing can rule out a push. Gated
# on the text naming both `git` and `push` so an unrelated opaque wrapper is not
# answered with a push denial.
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	opaque_push_payload

	decision := {
		"rule_id": "ER-EFFECTS-BLOCK-ANY-PUSH",
		"reason": "This command wraps a shell payload the guard cannot read while naming git and push, so it cannot be shown not to push. Run the git command directly, in a quoted argument.",
		"severity": "CRITICAL",
	}
}

executed_texts := commands.input_executed_texts

any_executed_push if {
	some text in executed_texts
	is_git_push(lower(text))
}

opaque_push_payload if {
	commands.input_unparsed_shell_payload
	lowered := lower(input.tool_input.command)
	contains(lowered, "git")
	contains(lowered, "push")
}

# The same invocation pattern `git_block_main_push` uses, including global-option
# forms such as `git -C <repo> push`. Kept as its own copy rather than imported
# so that guard stays readable on its own and a change to one cannot silently
# move the other.
git_push_command_pattern := `(^|[;&|(
])\s*(command\s+)?git([ \t]+((-c|--git-dir|--work-tree|--namespace|--config-env)(=|[ \t]+)("[^"\n]*"|'[^'\n]*'|[^ \t;&|()\n]+)|--(bare|no-pager|paginate|literal-pathspecs|no-replace-objects|exec-path)(=("[^"\n]*"|'[^'\n]*'|[^ \t;&|()\n]+))?))*[ \t]+push([ \t;&|)\n]|$)`

is_git_push(cmd) if {
	regex.match(git_push_command_pattern, cmd)
}
