# METADATA
# scope: package
# title: Lints and formatters run only when the user has authorized a commit
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-LINT-BEFORE-COMMIT
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
#     required_signals: ["commit_authorization"]
package cupcake.policies.claude.no_lint_before_commit

import rego.v1

import data.cupcake.system.commands

# User directive 2026-10-17, verbatim: "Lets have a subagent policy to never run
# lints or formatting or perform formatting ourselves until we are just about to
# commit, and only commit when authorized".
#
# "Just about to commit" has one checkable meaning: the user's own last typed
# message is `authorize commit` and no commit has spent it yet. The
# `commit_authorization` signal answers that in its second field, `LIVE` or
# `DENIED`; a subagent always gets `DENIED`. This file decides only what counts as
# a lint or formatter run, and allows one only on `LIVE`.
#
# Lints and formatters, by program: `cargo fmt`, `cargo clippy` (also behind
# `xwin` or a `+toolchain`), `rustfmt`, `shellcheck`, `ruff`, `black`, `prettier`
# and the other tools in `lint_tools`, `scripts/check.sh` in any stage (which
# `no_whole_check_sh` also refuses outright), `scripts/act-check.sh`, the comment
# caps gate and the lossy UTF-8 gate. And any `--fix` sweep: a script, cargo or
# lint tool handed `--fix`.
#
# Not lints, and not touched here: a gate's `--selftest`, `cargo test`, `opa test`,
# and running a script to produce data. The edit-time comment caps guard
# (`edit_no_comment_caps_guard`) is a different thing, a refusal of the written
# sentence itself, and is unchanged.
#
# Only a program a shell would run counts, read from the executed decomposition in
# `.cupcake/system/commands.rego` the way `require_scoped_cargo` reads it, so a
# commit message, a `bd remember` body or a heredoc body that names `cargo fmt`
# is prose. No regex: token operations only, for the reason recorded in
# `no_whole_check_sh`.
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	runs_lint
	not lint_live

	decision := {
		"rule_id": "ER-EFFECTS-NO-LINT-BEFORE-COMMIT",
		"reason": deny_reason,
		"severity": "HIGH",
	}
}

lint_live if {
	signal_fields[1] == "LIVE"
}

signal_value := v if {
	v := input.signals.commit_authorization
	is_string(v)
} else := v if {
	v := input.signals.commit_authorization.output
	is_string(v)
} else := ""

signal_fields := split(trim(signal_value, " \r\n"), "\t")

signal_detail := d if {
	signal_fields[1] != "LIVE"
	d := signal_fields[2]
	d != ""
} else := "the authorization reader gave no answer"

deny_reason := concat("", [
	"Lints and formatters run only when the user is about to commit (", signal_detail, "). ",
	"A subagent never runs them. The main session runs them only after the user's own reply `authorize commit` and before the commit that reply pays for. ",
	"Until then leave formatting and lint findings alone and keep working; tests (`cargo test -p <crate>`, `opa test`, a gate's `--selftest`) are not lints and stay allowed.",
])

# --- detection -----------------------------------------------------------------

executed_word_lists contains words if {
	some text in commands.input_executed_texts
	words := commands.command_slot_words(replace(replace(text, "\r", "\n"), "\n", " ; "))
}

runs_lint if {
	some words in executed_word_lists
	spans := commands.data_heredoc_spans(words)
	some index, word in words
	lint_candidate(words, index, word)
	not commands.index_inside(spans, index)
	not commands.operand_shaped(words, index)
	in_command_slot(words, index)
}

lint_tools := {
	"rustfmt", "cargo-fmt", "cargo-clippy", "clippy-driver",
	"shellcheck", "shfmt",
	"ruff", "black", "isort", "flake8", "pylint", "mypy",
	"prettier", "eslint", "biome", "dprint",
	"taplo", "clang-format", "yamllint", "markdownlint", "stylua",
}

# Gate scripts that are lints, and whether a `--selftest` turns them into a test.
lint_scripts := {"check.sh", "act-check.sh", "check-comment-caps.py", "check-no-lossy-utf8.py"}

selftest_exempt_scripts := {"check-comment-caps.py", "check-no-lossy-utf8.py"}

lint_candidate(words, index, word) if {
	basename(word) in lint_tools
}

lint_candidate(words, index, word) if {
	cargo_word(word)
	subcommand(invocation_args(words, index)) in {"fmt", "clippy"}
}

lint_candidate(words, index, word) if {
	name := basename(word)
	name in lint_scripts
	not selftest_run(words, index, name)
}

# A `--fix` sweep by anything that is plausibly a fixer: a script by path, cargo,
# or a lint tool. Limited to those so a `bd remember` body naming the flag is not
# read as one.
lint_candidate(words, index, word) if {
	fixer_program(word)
	some arg in invocation_args(words, index)
	fix_flag(arg)
}

selftest_run(words, index, name) if {
	name in selftest_exempt_scripts
	"--selftest" in invocation_args(words, index)
}

fix_flag(arg) if arg == "--fix"

fix_flag(arg) if startswith(arg, "--fix=")

fixer_program(word) if cargo_word(word)

fixer_program(word) if basename(word) in lint_tools

fixer_program(word) if {
	some suffix in {".py", ".sh", ".js", ".mjs"}
	endswith(bare_word(word), suffix)
}

bare_word(word) := trim(word, "\"'`(){}")

basename(word) := name if {
	parts := split(bare_word(word), "/")
	name := parts[count(parts) - 1]
}

cargo_word(word) if bare_word(word) == "cargo"

cargo_word(word) if endswith(bare_word(word), "/cargo")

# Launchers that run the program named after them: `npx prettier`, `pipx run
# black`, `pnpm exec eslint`. The shared slot test already steps over shells,
# interpreters (`python3 -m black`), `uv run` and `uvx`.
launcher_words := {"{", "!", "time", "xargs", "npx", "bunx", "pnpx", "pipx", "pnpm", "yarn", "npm", "dlx"}

extra_slot_prefix(words, position) if words[position] in launcher_words

extra_slot_prefix(words, position) if {
	words[position] in {"run", "exec"}
	position > 0
	words[position - 1] in {"pipx", "pnpm", "yarn", "npm"}
}

in_command_slot(words, index) if {
	start := commands.command_slot_start(words, index)
	count([position |
		some position, _ in words
		position >= start
		position < index
		not commands.command_slot_wrapper_at(words, position)
		not extra_slot_prefix(words, position)
	]) == 0
}

# The words after the program up to the end of its statement, cut at a bare `--`.
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

# The first argument that is not an option, a `+toolchain`, or the `xwin` of
# `cargo xwin clippy`.
subcommand(args) := sub if {
	positional := [word |
		some word in args
		not startswith(word, "-")
		not startswith(word, "+")
		word != "xwin"
	]
	sub := bare_word(positional[0])
}
