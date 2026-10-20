# METADATA
# scope: package
# title: Require an explicit --jobs on every er-builds-pvp.py run
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-REQUIRE-PVP-JOBS
#   description: >-
#     Hard block on running scripts/er-builds-pvp.py without `--jobs N`. Its default is
#     `os.cpu_count() - 1` forked workers, 15 on this 16-core box, and agents run several
#     rankings at once. Measured 2026-10-01: one unscoped `--rl 150 --top 1000 --json` run
#     held 17 processes while four other rankings ran beside it, load average 33 on 16 cores.
#     Every worker already sits at nice 19 with idle I/O, so lowering priority does not help;
#     the only lever is the worker count, and choosing it is the caller's job.
#
#     Exempt by shape: `--selftest`, `--help`/`-h`. Only an execution counts: the script
#     named as an operand of `sed`, `grep`, `cat`, `git`, `bd` or any other program is not a run.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.require_jobs_er_builds_pvp

import rego.v1

import data.cupcake.system.commands

command := object.get(input.tool_input, "command", "")

# Whitespace-normalized, for the reason require_scoped_cargo gives: the live engine collapses
# unquoted newlines before any policy runs, `opa test` does not.
norm_command := concat(" ", [word |
	some word in split(replace(replace(replace(command, "\t", " "), "\r", " "), "\n", " "), " ")
	word != ""
])

# A run, not a mention, read from the shared decomposition in `.cupcake/system/commands.rego`:
# every text the shell executes (a `bash -c '...'` payload as a text of its own), with the
# anchors inside quoted operands blanked, split into words with each separator standing alone.
# The script runs when its word stands in a command slot: first in its statement, or behind
# nothing but wrappers -- `python3`, `python3 -u`, `uv run --with x python3`, `timeout 30`.
#
# The previous version matched a regex over the command with quoted spans cut out by
# quote-index parity alone. A quote inside a quoted span flips that parity, text from inside a
# `sed` script lands outside the quotes, and on 2026-10-19 a subagent's `sed -i` edit of this
# script was refused as an unscoped ranking run. Here the program of the statement decides:
# behind `sed`, `grep`, `cat`, `git`, `bd` or `python3 -c`, the script name is an operand.
executed_texts := commands.input_executed_texts

pvp_script_word(word) if {
	parts := split(word, "/")
	parts[count(parts) - 1] == "er-builds-pvp.py"
}

pvp_invoked if {
	some text in executed_texts
	words := commands.command_slot_words(text)
	pvp_runs_in(words)
}

# A newline ends a statement as `;` does, as in `no_whole_check_sh`.
pvp_invoked if {
	some text in executed_texts
	words := commands.command_slot_words(replace(replace(text, "\r", "\n"), "\n", " ; "))
	pvp_runs_in(words)
}

pvp_runs_in(words) if {
	spans := commands.data_heredoc_spans(words)
	some index, word in words
	pvp_script_word(word)
	not commands.index_inside(spans, index)
	not commands.operand_shaped(words, index)
	commands.word_in_command_slot(words, index)
}

# `--jobs N` or `--jobs=N` with a positive integer. Argparse also accepts the unambiguous
# prefix `--job`/`--jo`; those are refused on purpose so the count is always spelled out.
has_jobs if {
	regex.match("(^|[[:space:]])--jobs([[:space:]]+|=)[1-9][0-9]*($|[[:space:];|&)'\"])", norm_command)
}

non_ranking_mode if {
	regex.match("(^|[[:space:]])(--selftest|--help|-h)($|[[:space:];|&)'\"])", norm_command)
}

block_reason := "🧁 Cupcake blocked an er-builds-pvp.py run with no `--jobs`. Its default is cpu_count - 1 forked workers (15 here), and rankings run several at a time: on 2026-10-01 one unscoped run held 17 processes beside four other rankings, load average 33 on 16 cores. The workers already run at nice 19 with idle I/O, so the worker count is the only lever. Pass it: `--jobs 4` for a full RL window ranking, `--jobs 1` or `--jobs 2` for a `--weapon`-scoped run. `--selftest` and `--help` are exempt."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	pvp_invoked
	not has_jobs
	not non_ranking_mode

	decision := {
		"rule_id": "ER-EFFECTS-REQUIRE-PVP-JOBS",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nSource: ", command]),
	}
}
