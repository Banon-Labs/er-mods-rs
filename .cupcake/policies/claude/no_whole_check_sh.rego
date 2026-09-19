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
executed_texts := commands.executed_texts(input.tool_input.command)

# `check.sh` reached from a command slot, however it is spelled: `bash
# scripts/check.sh`, `sh ./scripts/check.sh`, an absolute path, the script invoked
# directly, or any of those behind `timeout`/`env`/an assignment.
#
# Token operations rather than one pattern. The engine evaluates policies as wasm
# modules, and a regex in a rule crashed every policy at once on 2026-09-14 (bd
# `a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14`);
# `teardown_must_relaunch` was rewritten off regexes for the same reason.
check_script_word(word) if endswith(word, "check.sh")

# A word that cannot be the separator it looks like, because `executed_texts` has
# already neutralised the separators inside quoted spans. Kept as a distinct token
# rather than collapsed to a space so command position survives the split -- the
# first draft of this file replaced separators with spaces, lost that information,
# and denied a commit message that merely named the command.
separator := "__cupcake_check_sh_sep__"

# Spaces either side, so a separator written tight against its neighbours
# (`a;b`, `x&&y`) still splits into three words rather than one.
marker := " __cupcake_check_sh_sep__ "

words_of(text) := [word |
	marked := replace(replace(replace(replace(text, ";", marker), "&", marker), "|", marker), "(", marker)
	some word in split(marked, " ")
	word != ""
]

# Words that may stand between a command slot and the thing it runs without the
# thing stopping being a command: the shells, the exec wrappers, their flags and
# numeric arguments, and leading `VAR=value` assignments.
#
# The set is closed on purpose and short. Anything outside it -- `git`, `cat`,
# `python3`, `bd` -- means the words after it are that command's OPERANDS, which
# is the whole of how `git commit -m "... bash scripts/check.sh"` stays allowed.
wrapper_word(word) if endswith(word, "bash")

wrapper_word(word) if endswith(word, "sh")

wrapper_word(word) if endswith(word, "zsh")

wrapper_word(word) if endswith(word, "env")

wrapper_word("command")

wrapper_word("timeout")

wrapper_word("nice")

wrapper_word("nohup")

wrapper_word("setsid")

wrapper_word("stdbuf")

wrapper_word("exec")

wrapper_word("sudo")

wrapper_word(word) if startswith(word, "-")

wrapper_word(word) if regex.match(`^[0-9]+$`, word)

# `VAR=value`, which is a prefix to a command rather than a command. Excludes a
# path-looking operand so `--out=/some/path` is not read as an assignment.
wrapper_word(word) if {
	contains(word, "=")
	not contains(word, "/")
}

# The index just past the nearest separator before `index`, or 0 when there is
# none: where the command containing `index` begins.
slot_start(words, index) := start if {
	befores := [position |
		some position, word in words
		position < index
		word == separator
	]
	start := max(array.concat(befores, [-1])) + 1
}

# True when every word from the start of this command up to `index` is a wrapper,
# which is what makes the word at `index` the thing being run.
in_command_slot(words, index) if {
	start := slot_start(words, index)
	count([position |
		some position, word in words
		position >= start
		position < index
		not wrapper_word(word)
	]) == 0
}

runs_check_sh if {
	some text in executed_texts
	words := words_of(text)
	some index, word in words
	check_script_word(word)
	in_command_slot(words, index)
}

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	runs_check_sh

	decision := {
		"rule_id": "ER-EFFECTS-NO-CHECK-SH",
		"reason": "The agent does not run scripts/check.sh. The pre-push hook runs it and CI runs it, on a box with no game on it. Run the specific gate your edit touched instead, by its own path: `cargo test -p <crate>`, `cargo fmt -p <crate> -- --check`, `python3 scripts/check-<gate>.py`. Naming them is your work, and it is seconds.",
		"severity": "HIGH",
	}
}
