# METADATA
# scope: package
# title: The Agent Commits Only On The User's Exact Authorization
# authors: ["er-quickload agents"]
# custom:
#   severity: CRITICAL
#   id: ER-EFFECTS-COMMIT-NEEDS-AUTHORIZATION
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
#     required_signals: ["commit_authorization"]
package cupcake.policies.claude.git_commit_authorization

import rego.v1

import data.cupcake.system.commands

# User directive 2026-10-17, verbatim: "Lets have a subagent policy to never run
# lints or formatting or perform formatting ourselves until we are just about to
# commit, and only commit when authorized instead of what your agent just did
# which is tank my FPS with a commit to a worktree when the files wouldn't be
# lost anyway because we are in a virgin worktree in a persistent drive."
#
# What happened: a subagent committed in a gitignored worktree on a persistent
# drive. The commit preserved nothing the worktree was not already keeping, and
# its hooks ran the check suite while the user was playing a game.
#
# So: no commit, in any repository or worktree, `git -C <dir>` and chained forms
# included, except one commit per user message that is exactly
# `authorize commit`. A subagent never commits. Everything that decides that lives
# in the `commit_authorization` signal (`scripts/cupcake_commit_authorization.py`),
# which reads the session transcript the way the push signal does and shares its
# reader. This file allows a commit only on the signal's `AUTHORIZED` field, so an
# absent, crashed or timed-out signal denies.
#
# The design is `git_block_any_push`'s, and so is the reason it is a rule rather
# than a memory: a memory is advisory, and the agent that caused this had read
# the instructions that told it when to commit.
#
# No new regex shape enters the rulebook: the commit pattern below is the one
# `git_block_main_commit` already runs, applied per segment the way
# `git_block_any_push` counts its pushes.
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	any_executed_commit
	not authorized_commit

	decision := {
		"rule_id": "ER-EFFECTS-COMMIT-NEEDS-AUTHORIZATION",
		"reason": deny_reason,
		"severity": "CRITICAL",
	}
}

# Fail closed on a payload this guard cannot read while the command names git and
# commit: `bash -c $CMD` hands a shell a program that is not in the command text.
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	opaque_commit_payload

	decision := {
		"rule_id": "ER-EFFECTS-COMMIT-NEEDS-AUTHORIZATION",
		"reason": "This command wraps a shell payload the guard cannot read while naming git and commit, so it cannot be shown not to commit. Run the git command directly.",
		"severity": "CRITICAL",
	}
}

executed_texts := commands.input_executed_texts

any_executed_commit if {
	some text in executed_texts
	is_git_commit(lower(text))
}

opaque_commit_payload if {
	commands.input_unparsed_shell_payload
	lowered := lower(input.tool_input.command)
	contains(lowered, "git")
	contains(lowered, "commit")
}

# --- the authorization ---------------------------------------------------------
#
# The signal refuses anything but one plain commit. The count below is a second,
# independent reading: exactly one commit among everything this command executes,
# and the command itself starts with `git`, so nothing can ride along on an
# authorization even if the signal's parser were wrong.
authorized_commit if {
	signal_fields[0] == "AUTHORIZED"
	count(signal_fields) == 3
	startswith(trim_left(lower(input.tool_input.command), " \t"), "git ")
	commit_invocation_count == 1
	not opaque_commit_payload
	not chained
}

# A separator outside quotes means something runs beside the commit. Quoted spans
# are already blanked in the executed texts, so a `;` inside a message is not one;
# a text the decomposition could not blank (a backtick anywhere) keeps its raw
# form and denies, which is the direction to err in.
chained if {
	some text in executed_texts
	some separator in {";", "&", "|"}
	contains(text, separator)
}

separators := {"&&": ";", "||": ";", "|": ";", "&": ";", "\n": ";", "(": ";", ")": ";"}

commit_invocation_count := count([segment |
	some text in executed_texts
	some segment in split(strings.replace_n(separators, lower(text)), ";")
	is_git_commit(segment)
])

signal_value := v if {
	v := input.signals.commit_authorization
	is_string(v)
} else := v if {
	v := input.signals.commit_authorization.output
	is_string(v)
} else := ""

signal_fields := split(trim(signal_value, " \r\n"), "\t")

signal_detail := d if {
	d := signal_fields[2]
	d != ""
} else := "the authorization reader gave no answer"

deny_reason := concat("", [
	"The agent does not commit without the user's authorization (", signal_detail, "). ",
	"A subagent never commits. In the main session, ask the user to reply with exactly: `authorize commit` ",
	"-- that message and nothing else, as their next reply. It pays for one commit; ",
	"lints and formatters are allowed only between that reply and the commit it pays for. ",
	"The commit must then be one plain command with nothing chained to it: `git [-C <dir>] commit -F <file>` ",
	"or `git commit -m '<single-quoted message>'`. ",
	"Until then leave the changes uncommitted: a worktree on a persistent drive already keeps them.",
])

# The invocation pattern `git_block_main_commit` uses, including global-option
# forms such as `git -C <repo> commit`. Kept as its own copy so that guard stays
# readable on its own and a change to one cannot silently move the other.
git_commit_command_pattern := `(^|[;&|(
])\s*(command\s+)?git([ \t]+((-c|--git-dir|--work-tree|--namespace|--config-env)(=|[ \t]+)("[^"\n]*"|'[^'\n]*'|[^ \t;&|()\n]+)|--(bare|no-pager|paginate|literal-pathspecs|no-replace-objects|exec-path)(=("[^"\n]*"|'[^'\n]*'|[^ \t;&|()\n]+))?))*[ \t]+commit([ \t;&|)\n]|$)`

is_git_commit(cmd) if {
	regex.match(git_commit_command_pattern, cmd)
}
