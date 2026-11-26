# METADATA
# scope: package
# title: The Agent Pushes Only On The User's Exact Authorization
# authors: ["er-quickload agents"]
# custom:
#   severity: CRITICAL
#   id: ER-EFFECTS-BLOCK-ANY-PUSH
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
#     required_signals: ["push_authorization"]
package cupcake.policies.claude.git_block_any_push

import rego.v1

import data.cupcake.system.commands

# User directive 2026-09-17, verbatim: "I stopped the monitor, because quite
# frankly you should NEVER be pushing." Restated the same day as "No push. Push
# bad" after an agent pushed anyway, which is why this file exists: the directive
# had been recorded as a `bd` memory and a memory is advisory, so the next agent
# in the same session read a red CI hook and pushed to fix the remote.
#
# User directive 2026-10-07, verbatim: "update the git push blocking policy to
# give you an escape hatch if my last response is the same code word you provide
# me, like "authorize <SHA>" so you can push if and only if that's what my
# message contains".
#
# So the rule is now: no push, except one push of one branch whose tip the
# user's last typed message names, the whole message being `authorize <sha>`.
# Everything that decides that lives in the `push_authorization` signal
# (`scripts/cupcake_push_authorization.py`), which reads the session transcript
# and the branch tip itself. This file allows a push only on its `AUTHORIZED`
# line, so an absent, crashed or timed-out signal denies exactly as before.
#
# What has not changed. A CI-state report is context, never an instruction to
# update the remote. `scripts/er-push-watched.sh` is still refused outright, and
# so is any shell payload the guard cannot read. Main and master are never
# authorizable, nor are force, delete, mirror, tags, all, or a `+`/`:` refspec:
# the signal accepts only `git [-C <dir>] push [-u] <remote> <branch>`.
#
# Two concrete costs behind the original rule, both measured on this machine.
# The pre-push hook runs the full check suite: ten minutes with every core
# pinned, so an authorized push belongs in a background task. And the remote is
# the user's to publish -- which is why the authorization has to come from their
# own typed reply and is spent by the push it was given for.
#
# No new regex enters the rulebook here: the reason text is built from string
# splits, and a regex added here is a hazard to every policy at once (see bd
# `a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14`).
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	any_executed_push
	not authorized_push

	decision := {
		"rule_id": "ER-EFFECTS-BLOCK-ANY-PUSH",
		"reason": deny_reason,
		"severity": "CRITICAL",
	}
}

# The watched-push helper is the same act wearing a script name, and the user
# named it when they gave the directive. No authorization lifts this one: the
# authorizable shape is a plain `git push`.
#
# Refused when it RUNS, not when it is named. Until 2026-11-24 this matched the
# substring `er-push-watched` anywhere in the command, and the same day refused two
# read-only commands that only carried the path as an operand: a python printer
# handed `scripts/er-push-watched.sh:34`, and `sed -n '<range>p'` over the helper's
# own test script. The run is now read the way `no_whole_check_sh` and
# `require_jobs_er_builds_pvp` read theirs, from the shared decomposition in
# `.cupcake/system/commands.rego`: the script's word must stand in a command slot,
# first in its statement or behind nothing but wrappers (`bash`, `sh`, `source`,
# `.`, `env`, `timeout`, `nohup`, `setsid`, assignments), in any text the shell
# executes, `bash -c '...'` payloads included.
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	watched_helper_runs

	decision := watched_helper_decision
}

# A shell payload this guard cannot read that names the helper still fails closed,
# as the substring match did for it.
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	commands.input_unparsed_shell_payload
	contains(lower(input.tool_input.command), "er-push-watched")

	decision := watched_helper_decision
}

watched_helper_decision := {
	"rule_id": "ER-EFFECTS-BLOCK-ANY-PUSH",
	"reason": "`scripts/er-push-watched.sh` pushes, so the agent does not run it. Leave the commits local and say which ones are unpushed; with the user's `authorize <sha>` reply, push with a plain `git push <remote> <branch>`. Reading or printing the file is not refused.",
	"severity": "CRITICAL",
}

# The script's own word, by its last path part, so `scripts/...`, `./scripts/...`
# and an absolute path all match while `scripts/test-er-push-watched-*.sh` and
# `scripts/er-push-watched.sh:34` do not.
watched_helper_word(word) if {
	parts := split(lower(trim(word, "\"'")), "/")
	parts[count(parts) - 1] == "er-push-watched.sh"
}

watched_helper_runs if {
	some text in executed_texts
	watched_helper_runs_in(commands.command_slot_words(text))
}

# A newline ends a statement as `;` does, as in `no_whole_check_sh`.
watched_helper_runs if {
	some text in executed_texts
	watched_helper_runs_in(commands.command_slot_words(replace(replace(text, "\r", "\n"), "\n", " ; ")))
}

watched_helper_runs_in(words) if {
	spans := commands.data_heredoc_spans(words)
	some index, word in words
	watched_helper_word(word)
	not commands.index_inside(spans, index)
	not commands.operand_shaped(words, index)
	watched_helper_in_slot(words, index)
}

# The shared slot test, plus words it does not list that may run the program after
# them: `time`, `xargs` with its options, and a program named by a variable
# (`$SHELL x`, `"$CMD x"`), which may be a shell. Kept local so the shared wrapper
# set, which every command-slot guard reads, is not widened from here.
watched_helper_in_slot(words, index) if commands.word_in_command_slot(words, index)

watched_helper_in_slot(words, index) if {
	start := commands.command_slot_start(words, index)
	count([position |
		some position, _ in words
		position >= start
		position < index
		not commands.command_slot_wrapper_at(words, position)
		not watched_helper_extra_wrapper(words[position])
	]) == 0
}

watched_helper_extra_wrapper(word) if word in {"time", "xargs"}

watched_helper_extra_wrapper(word) if startswith(trim_left(word, "\"'"), "$")

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

# --- the authorization ---------------------------------------------------------
#
# The signal already refuses anything but one plain push. The count below is a
# second, independent reading of the same command: exactly one push among
# everything this command executes, so a wrapper or a second statement can never
# ride along on an authorization even if the signal's parser were wrong.
authorized_push if {
	signal_fields[0] == "AUTHORIZED"
	count(signal_fields) == 4
	startswith(trim_left(lower(input.tool_input.command), " \t"), "git ")
	push_invocation_count == 1
	not opaque_push_payload
	not forbidden_push_shape
}

# Push invocations across everything the command executes, wrapper payloads
# included -- not texts, since one text can hold `git push a; git push b`.
#
# Counted per shell segment with `regex.match` rather than with
# `regex.find_n` over the same pattern. Measured 2026-10-07: the `find_n` count
# let `opa test` allow an authorized push that `cupcake eval` (its wasm build)
# denied; the per-segment count agrees in both, which
# scripts/test-push-authorization-signal.py checks end to end.
separators := {"&&": ";", "||": ";", "|": ";", "&": ";", "\n": ";", "(": ";", ")": ";"}

push_invocation_count := count([segment |
	some text in executed_texts
	some segment in split(strings.replace_n(separators, lower(text)), ";")
	is_git_push(segment)
])

# The same refusals the signal makes, read again from the command words, so an
# `AUTHORIZED` line can never carry a force, a delete, a refspec or main.
allowed_push_options := {"-u", "--set-upstream", "-c"}

forbidden_push_shape if {
	some word in split(lower(input.tool_input.command), " ")
	startswith(word, "-")
	not word in allowed_push_options
}

forbidden_push_shape if {
	some word in split(lower(input.tool_input.command), " ")
	startswith(word, "+")
}

forbidden_push_shape if {
	some word in split(lower(input.tool_input.command), " ")
	contains(word, ":")
}

forbidden_push_shape if {
	some word in split(lower(input.tool_input.command), " ")
	trim(word, "'\"") in {"main", "master", "head"}
}

signal_value := v if {
	v := input.signals.push_authorization
	is_string(v)
} else := v if {
	v := input.signals.push_authorization.output
	is_string(v)
} else := ""

signal_fields := split(trim(signal_value, " \r\n"), "\t")

signal_branch := b if {
	b := signal_fields[1]
	b != "-"
	b != ""
}

signal_tip := t if {
	t := signal_fields[2]
	count(t) == 40
}

signal_detail := d if {
	d := signal_fields[3]
} else := "the authorization reader gave no answer"

deny_reason := msg if {
	msg := concat("", [
		"The agent does not push without the user's authorization (", signal_detail, "). ",
		"To push branch `", signal_branch, "`, ask the user to reply with exactly: `authorize ", signal_tip, "` ",
		"-- that message and nothing else, as their next reply. It authorizes one push of that tip only; ",
		"then run `git push <remote> ", signal_branch, "` (in the background: the pre-push hook runs the full check suite). ",
		"Main/master, force, delete, tags and refspecs are never authorizable. ",
		"A CI-state report is context, not an instruction to update the remote.",
	])
} else := msg if {
	msg := concat("", [
		"The agent does not push without the user's authorization (", signal_detail, "). ",
		"Only `git push <remote> <branch>` for a local non-main branch can be authorized, by the user replying with exactly `authorize <full tip sha of that branch>`. ",
		"Otherwise commit, leave the branch local, and say plainly which commits are unpushed. ",
		"A CI-state report is context, not an instruction to update the remote.",
	])
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
