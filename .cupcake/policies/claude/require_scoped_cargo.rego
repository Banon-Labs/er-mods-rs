# METADATA
# scope: package
# title: Require an explicitly named crate scope on agent cargo invocations
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-REQUIRE-SCOPED-CARGO
#   description: >-
#     Hard block on a whole-workspace cargo invocation from an agent Bash command:
#     a compiling subcommand with no `-p`/`--package`, or one that says
#     `--workspace`/`--all` outright. Naming what to build is the AGENT's work. It
#     is the same reasoning already done to decide what to edit, and delegating it
#     to the build system is what turns a thirty-second question into an hour,
#     because the build system's answer to an unscoped request is always "all of
#     it".
#
#     MEASURED 2026-09-02 (bd subagent-full-check-sh-sleep-poll-is-the-hour-long-tax-2026-09-02):
#     three er-npc-possess subagents each ran ~72 minutes. Almost none of that was
#     the research they were dispatched for -- the Ghidra MCP accounted for 51
#     calls totalling 59 seconds. What consumed the time was whole-workspace
#     validation, run repeatedly, in separate worktrees with separate target/ dirs
#     so no build cache was shared. Four ran concurrently on a 16-core box: load
#     average 54.26, and a gate that costs minutes on a quiet tree was still
#     unfinished at 19 minutes. Because it far outruns the Bash tool timeout each
#     agent then sleep-polled its own run -- 18x `sleep 118` = 2003s for one, 60x
#     `timeout 28 sleep 27` = 1330s for another -- and a poll costs a whole model
#     turn, not merely its sleep. One agent spent 33 of its 45 tool-minutes asleep.
#
#     `cargo test -p er-npc-possess` is seconds and answers the question the agent
#     actually has. The whole-workspace gate is the ORCHESTRATOR's job, run once at
#     integration on a quiet tree -- which is also the only condition under which
#     its verdict means anything, since scripts/check.sh reports NOT RUN and
#     INCONCLUSIVE steps and a contended box manufactures both. That restriction
#     lives INSIDE check.sh, which knows its own repo_root and so cannot be evaded
#     by cd'ing; it is deliberately NOT enforced here. A first draft of this policy
#     did deny the gate by name and immediately blocked the edit that was removing
#     it, because the file being edited contains the string -- the same defect
#     block_manual_pgrep documents as "a guard whose own removal cannot be
#     described in the commit that removes it is unwritable in the repo that
#     enforces it".
#
#     Only a `cargo` a shell would run counts: one in command position, in the
#     command itself or in a `bash -c` payload, outside quoted operands and
#     outside a heredoc body no shell reads. Exempt by shape, not by intent:
#     `cargo fmt` (whole-tree formatting is the point, and it compiles nothing),
#     the non-building `cargo metadata`/`tree`/`--version`, and a `-p`-scoped
#     invocation however many crates it names.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.require_scoped_cargo

import rego.v1

import data.cupcake.system.commands

command := object.get(input.tool_input, "command", "")

# --- detection ---------------------------------------------------------------
# A `cargo` counts only where a shell would run it: in command position of a
# statement, read from the executed decomposition in `.cupcake/system/commands.rego`.
#
# This used to be one regex over the whole command with quotes left in, so that
# `bash -c 'cargo build'` was caught. It also caught every quoted mention: on
# 2026-10-02 `git commit -m "..."` was denied because the message described a
# `cargo build`, and the git-commit text exemption that was meant to cover that
# shape gave up on any message holding a backtick, a parenthesis or a heredoc.
#
# The decomposition answers both halves without an exemption. A quoted operand has
# its separators blanked, so the words inside it follow `git`, `echo` or `bd` and are
# that command's operands, never a program. A shell wrapper's payload is decomposed
# as an executed text of its own, so `bash -c 'cargo build'` still has `cargo` at
# position 0. Command substitution keeps the raw text, so `$(cargo build)` still
# reads `(` as a separator and is denied. A heredoc body that no shell reads is
# skipped through `commands.data_heredoc_spans`, the same narrowing
# `no_whole_check_sh` uses; a body fed to a shell keeps its words.
#
# Unquoted newlines arrive in production as `; ` -- `scripts/cupcake-hook.sh`
# rewrites them before the engine sees the command -- while `opa test` sees the raw
# text. Turning the newline into a separator here makes both read the same
# statements. Newlines inside quotes were blanked by the decomposition already.
executed_word_lists contains words if {
	some text in commands.input_executed_texts
	words := commands.command_slot_words(replace(replace(text, "\r", "\n"), "\n", " ; "))
}

# Compiling subcommands. `cargo fmt`, `metadata`, `tree` and `--version` are not in it.
compiling_subcommand := {"build", "test", "check", "clippy", "bench", "doc"}

# `cargo`, `~/.cargo/bin/cargo`, `/usr/bin/cargo`. Quote characters are trimmed
# because the decomposition keeps them on the word (`"cargo" build` runs cargo),
# and a leading backtick marks a command substitution the raw text kept whole.
# Closing parentheses and braces are trimmed because the word split only cuts at
# an opening `(`, so `(cd x && cargo check)` ends on the word `check)`.
cargo_word(word) if bare_word(word) == "cargo"

cargo_word(word) if endswith(bare_word(word), "/cargo")

bare_word(word) := trim(word, "\"'`(){}")

# Words that stand before a program without being one and that the shared slot
# test does not know: a brace group, a negation, `time`, and `xargs`, whose next
# word is the program it runs. Kept here rather than added to
# `commands.command_slot_wrapper`, which every command-slot guard reads.
extra_slot_prefix(word) if word in {"{", "!", "time", "xargs"}

# The word at `index` is the program its statement runs.
in_command_slot(words, index) if {
	start := commands.command_slot_start(words, index)
	count([position |
		some position, _ in words
		position >= start
		position < index
		not commands.command_slot_wrapper_at(words, position)
		not extra_slot_prefix(words[position])
	]) == 0
}

# A backtick opening the word is a command substitution, which runs whatever
# precedes it in the text.
in_command_slot(words, index) if startswith(trim_left(words[index], "\"'"), "`")

# The words after `cargo` up to the end of its statement, and the part of those
# before a bare `--`: what follows `--` belongs to the test binary or the tool cargo
# runs, so a `--all` there is not cargo's.
invocation_args(words, index) := args if {
	ends := [position |
		some position, word in words
		position > index
		word == commands.command_slot_separator
	]
	end := min(array.concat(ends, [count(words)]))
	all_args := array.slice(words, index + 1, end)
	dashdash := [position |
		some position, word in all_args
		word == "--"
	]
	args := array.slice(all_args, 0, min(array.concat(dashdash, [count(all_args)])))
}

# The subcommand is the first argument that is not an option, a `+toolchain`, or
# the `xwin` of `cargo xwin build`.
subcommand(args) := sub if {
	positional := [word |
		some word in args
		not startswith(word, "-")
		not startswith(word, "+")
		word != "xwin"
	]
	sub := bare_word(positional[0])
}

# `-p`/`--package` in any accepted spelling: `-p x`, `-p=x`, `--package x`,
# `--package=x`. Naming several crates is still naming them.
package_flag(word) if bare_word(word) in {"-p", "--package"}

package_flag(word) if startswith(word, "-p=")

package_flag(word) if startswith(word, "--package=")

# `--workspace`/`--all` are the explicit spelling of the thing being blocked, so
# they never count as a scope even when paired with a `-p`.
whole_workspace_flag(word) if bare_word(word) in {"--workspace", "--all"}

unscoped_args(args) if {
	count([word |
		some word in args
		package_flag(word)
	]) == 0
}

unscoped_args(args) if {
	some word in args
	whole_workspace_flag(word)
}

unscoped_cargo if {
	some words in executed_word_lists
	spans := commands.data_heredoc_spans(words)
	some index, word in words
	cargo_word(word)
	not commands.index_inside(spans, index)
	in_command_slot(words, index)
	args := invocation_args(words, index)
	subcommand(args) in compiling_subcommand
	unscoped_args(args)
}

# --- decision ----------------------------------------------------------------

block_reason := "🧁 Cupcake blocked an UNSCOPED cargo invocation. Name the crates: `cargo test -p <crate>`, repeating -p for each one your change touches. Deciding which those are is your work, not the build system's -- its answer to an unscoped request is always 'all of it'. MEASURED 2026-09-02: three subagents each burned ~72 minutes, almost none of it on the research they were sent to do (the Ghidra MCP was 51 calls / 59s total). The cost was whole-workspace validation run repeatedly in worktrees with unshared target/ dirs: four concurrent runs, load average 54 on 16 cores, a gate still unfinished at 19 minutes, then 2003s and 1330s spent sleep-polling because it outruns the Bash timeout. `--workspace` and `--all` are the explicit spelling of the same thing and are blocked too. Exempt: `cargo fmt`, and the non-building `cargo metadata`/`tree`/`--version`. If a command may exceed the Bash timeout, launch it with run_in_background: true -- never `sleep N; tail log`, which converts wall time into model turns at 1:1. See bd subagent-full-check-sh-sleep-poll-is-the-hour-long-tax-2026-09-02."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	unscoped_cargo

	decision := {
		"rule_id": "ER-EFFECTS-REQUIRE-SCOPED-CARGO",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nSource: ", command]),
	}
}
