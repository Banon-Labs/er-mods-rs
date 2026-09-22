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
	some index, word in words
	check_script_word(word)
	commands.word_in_command_slot(words, index)
}

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	runs_check_sh

	decision := {
		"rule_id": "ER-EFFECTS-NO-CHECK-SH",
		"reason": "The agent does not run scripts/check.sh. The pre-push hook runs it and CI runs it, on a box with no game on it. Run the specific gate your edit touched instead, by its own path -- and in the MODE check.sh runs it, which is where this list used to mislead: `cargo test -p <crate>` AND `cargo clippy -p <crate> --all-targets` (the workspace denies warnings, and `cargo test` never invokes clippy), `cargo fmt -p <crate> -- --check`, and `python3 scripts/check-<gate>.py --selftest` BEFORE `python3 scripts/check-<gate>.py` (most gates have both, check.sh runs both, and a gate that passes on your tree can still have a broken selftest). Touching a scripts/*.py gate also means `python3 scripts/check-stages.py --selftest`, which proves every path a gate reads is declared by its stage. Naming them is your work, and it is seconds.",
		"severity": "HIGH",
	}
}
