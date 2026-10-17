# METADATA
# scope: package
# title: The agent does not run check.sh
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-CHECK-SH
#   description: >-
#     Hard block on an agent Bash command that runs `scripts/check.sh`, in any
#     form, with or without `--stage`. The pre-push hook runs it and CI runs it,
#     both on a box with no game on it, and neither needs the agent's help.
#
#     The user asked for this as a question on 2026-09-18 -- "why would I ever
#     want to run check.sh manually? And why would I want to allow you to run it
#     ever?" -- while a suite this agent had started in the background pinned
#     every core under a game this agent had also launched for them. The agent
#     answered with a `bd` memory and a refusal inside `check.sh`, and the user
#     rejected both: "If it's a rule, then you're bound by it. Since you weren't
#     bound by it, and recorded anyway, it's not a rule. Rego policies are rules."
#     A first draft of this file still exempted `--stage`; the user removed that
#     too -- "How about never run check.sh period?"
#
#     That is the whole reasoning. A memory is advisory and the next agent reads
#     it or does not. A refusal inside `check.sh` fires only once the command is
#     already running, and it is the agent's own code, which is the thing under
#     question. Only a `PreToolUse` deny stops the agent before the act.
#
#     What the agent runs instead is the specific gate its edit touched, named
#     directly and by its own path -- `cargo test -p <crate>`,
#     `cargo fmt -p <crate> -- --check`, `python3 scripts/check-<gate>.py`. Naming
#     them is the agent's work; it is the same reasoning already done to decide
#     what to edit, and `require_scoped_cargo` records at length what delegating
#     it to the build system costs.
#
#     Deliberately no override. `git_block_any_push` is the model: an escape hatch
#     an agent can type is an escape hatch an agent will type. The person who
#     wants the suite runs it themselves -- policies gate the agent's Bash tool,
#     never the user's terminal.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.no_whole_check_sh

import rego.v1

import data.cupcake.system.commands

# The executed decomposition, not the raw command string, for the reason recorded
# at length in `.cupcake/system/commands.rego`: anchoring on lexical position is
# wrong in both directions at once. It misses `bash -c 'bash scripts/check.sh'`,
# a wrapper form AGENTS.md actively recommends for fish, and it fires on a `bd`
# memory body, a commit message or a doc that merely quotes the command.
#
# That second half is what makes this file writable at all. `require_scoped_cargo`
# records a first draft of itself that "did deny the gate by name and immediately
# blocked the edit that was removing it, because the file being edited contains
# the string", and concludes that a guard naming this script cannot be written.
# It can, on this decomposition, and the tests pin both directions -- including a
# commit message and a `bd remember` body naming the command.
executed_texts := commands.input_executed_texts

# `check.sh` reached from a command slot, however it is spelled: `bash
# scripts/check.sh`, `sh ./scripts/check.sh`, an absolute path, the script invoked
# directly, or any of those behind `timeout`/`env`/an assignment.
#
# Token operations rather than one pattern. The engine evaluates policies as wasm
# modules, and a regex in a rule crashed every policy at once on 2026-09-14 (bd
# `a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14`);
# `teardown_must_relaunch` was rewritten off regexes for the same reason.
check_script_word(word) if endswith(word, "check.sh")

# The word split and the command-slot test were written here first and now live in
# `.cupcake/system/commands.rego`, because the next two policies that needed them were
# about to transcribe them (`teardown_must_relaunch`, `bash_no_python_file_write`) and a
# second copy of a decomposition is the divergence bug that package's own header warns
# about. The behaviour is unchanged; the reasoning that produced it is recorded there.
runs_check_sh if {
	some text in executed_texts
	words := commands.command_slot_words(text)
	spans := commands.data_heredoc_spans(words)
	some index, word in words
	check_script_word(word)

	# Before the command-slot test rather than after it: the slot test walks back
	# through the word list, and a body that names the script on every line would pay
	# for that walk once per mention.
	not commands.index_inside(spans, index)
	commands.word_in_command_slot(words, index)
	not read_without_executing(words, index)
}

# A shell handed `-n` reads the script and executes none of it: `man bash` documents the
# flag as "Read commands but do not execute them. This may be used to check a shell script
# for syntax errors", and `-o noexec` as "Same as -n". `bash -n scripts/check.sh` is a
# syntax check, and it was denied on 2026-10-13 as a run of the suite.
#
# The exemption is narrow on purpose. The shell must be the nearest one before the script
# in the same command, and every word between them must be one of that shell's own options,
# so `nice -n 10 bash scripts/check.sh` and `sudo -n bash scripts/check.sh` still deny: the
# `-n` there belongs to the wrapper. Anything this cannot read as an option -- a `+n` that
# switches the flag back off, a word it does not recognise -- leaves the deny standing.
# Each mention of the script is judged on its own, so a syntax check chained in front of a
# real run does not excuse the run.
read_without_executing(words, index) if {
	start := commands.command_slot_start(words, index)
	shells := [position |
		some position, word in words
		position >= start
		position < index
		noexec_shell(word)
	]
	count(shells) > 0
	options := array.slice(words, max(shells) + 1, index)
	every position, _ in options {
		shell_option_word(options, position)
	}
	some position, _ in options
	noexec_option(options, position)
	not interactive_option(options)
}

# The shells whose `-n` is documented as no-exec. A path is judged by its last part, so
# `/bin/bash` and `/usr/bin/env bash` read the same as `bash`.
noexec_shell(word) if {
	parts := split(word, "/")
	parts[count(parts) - 1] in {"sh", "bash", "zsh", "ksh", "mksh", "dash", "ash"}
}

# A cluster of single-letter options, `-n` or `-xn`, as against a long `--norc`.
short_option_cluster(word) if {
	startswith(word, "-")
	not startswith(word, "--")
	word != "-"
}

# The options that take the next word as their value: `-o name` (and a cluster ending in
# it, `-eo name`), `-O shopt_name`, and the two long options that name a file.
valued_option(word) if {
	short_option_cluster(word)
	endswith(word, "o")
}

valued_option(word) if {
	short_option_cluster(word)
	endswith(word, "O")
}

valued_option(word) if word in {"--rcfile", "--init-file"}

option_value(options, position) if {
	position > 0
	valued_option(options[position - 1])
}

shell_option_word(options, position) if startswith(options[position], "-")

shell_option_word(options, position) if option_value(options, position)

noexec_option(options, position) if {
	short_option_cluster(options[position])
	contains(options[position], "n")
	not option_value(options, position)
}

# The long spelling is zsh's (`man zsh`: options "may also be specified by name in GNU long
# option style", with dashes in the name ignored). Bash's manual lists no such long option,
# so there it is an invalid option rather than a way to run the script.
noexec_option(options, position) if options[position] in {"--noexec", "--no-exec"}

noexec_option(options, position) if {
	options[position] == "noexec"
	position > 0
	short_option_cluster(options[position - 1])
	endswith(options[position - 1], "o")
}

# The same manual page says `-n` "is ignored by interactive shells", and `-i` makes one.
interactive_option(options) if {
	some position, word in options
	short_option_cluster(word)
	contains(word, "i")
	not option_value(options, position)
}

# A heredoc body no shell reads is prose (2026-09-22): a pull-request body written with
# `cat > <file> <<'EOF' ... EOF` once handed this rule a command slot per parenthesis.
# The span computation was written here first and now lives in
# `.cupcake/system/commands.rego` as `data_heredoc_spans`, beside the measured denial
# that produced it and its residue, because `require_scoped_cargo` needed the same answer.

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	runs_check_sh

	decision := {
		"rule_id": "ER-EFFECTS-NO-CHECK-SH",
		"reason": "The agent does not run scripts/check.sh. The pre-push hook runs it and CI runs it, on a box with no game on it. Run the specific tests your edit touched instead, by their own path: `cargo test -p <crate>`, and `python3 scripts/check-<gate>.py --selftest` (a gate that passes on your tree can still have a broken selftest). Touching a scripts/*.py gate also means `python3 scripts/check-stages.py --selftest`, which proves every path a gate reads is declared by its stage. The lints -- `cargo clippy -p <crate> --all-targets` (the workspace denies warnings, and `cargo test` never invokes clippy), `cargo fmt -p <crate> -- --check`, `scripts/check-comment-caps.py` and `scripts/check-no-lossy-utf8.py` -- wait until the user replies `authorize commit` (user directive 2026-10-17), and a subagent never runs them. Naming them is your work, and it is seconds.",
		"severity": "HIGH",
	}
}
