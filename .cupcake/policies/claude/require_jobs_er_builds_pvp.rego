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
#     Exempt by shape: `--selftest`, `--help`/`-h`, and the text-only positions every guard
#     here shares (a single non-chained bd command, a git commit message).
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.require_jobs_er_builds_pvp

import rego.v1

command := object.get(input.tool_input, "command", "")

# Whitespace-normalized, for the reason require_scoped_cargo gives: the live engine collapses
# unquoted newlines before any policy runs, `opa test` does not.
norm_command := concat(" ", [word |
	some word in split(replace(replace(replace(command, "\t", " "), "\r", " "), "\n", " "), " ")
	word != ""
])

escapes_stripped := replace(replace(norm_command, `\"`, ""), `\'`, "")

double_parts := split(escapes_stripped, `"`)

outside_double := concat(" ", [double_parts[idx] |
	some idx
	double_parts[idx]
	idx % 2 == 0
])

single_parts := split(outside_double, "'")

unquoted_command := concat(" ", [single_parts[idx] |
	some idx
	single_parts[idx]
	idx % 2 == 0
])

# A run, not a mention: the script as the first argument of a python interpreter (`python3`,
# `python3.13`, `uv run [--with x] python3`), or executed directly at command start or after a
# shell separator, under any path prefix. A `sed`/`cat`/`python3 -c` read of the file is not a
# run. The trailing class keeps `er-builds-pvp.pyc` and `.py.bak` from matching.
pvp_pattern := "((^|[[:space:];|&('\"`/])python[0-9.]*[[:space:]]+(-[A-Za-z]+[[:space:]]+)*|(^|[;|&][[:space:]]*))([^[:space:];|&('\"`]*/)?er-builds-pvp\\.py($|[^[:alnum:]_.-])"

pvp_invoked if {
	regex.match(pvp_pattern, norm_command)
}

# `--jobs N` or `--jobs=N` with a positive integer. Argparse also accepts the unambiguous
# prefix `--job`/`--jo`; those are refused on purpose so the count is always spelled out.
has_jobs if {
	regex.match("(^|[[:space:]])--jobs([[:space:]]+|=)[1-9][0-9]*($|[[:space:];|&)'\"])", norm_command)
}

non_ranking_mode if {
	regex.match("(^|[[:space:]])(--selftest|--help|-h)($|[[:space:];|&)'\"])", norm_command)
}

text_mention_only if {
	bd_text_command
	not regex.match(pvp_pattern, unquoted_command)
}

text_mention_only if {
	git_commit_text_command
	not regex.match(pvp_pattern, unquoted_command)
}

bd_text_command if {
	regex.match(`^[[:space:]]*((\$HOME|\$\{HOME\}|~|/home/[[:alnum:]._-]+|/root|/Users/[[:alnum:]._-]+)/\.local/bin/)?bd[[:space:]]+(create|update|comment|comments|remember|close)([[:space:]]|$)`, norm_command)
	not regex.match(`[;|&()<>\x60]`, unquoted_command)
	not contains(command, "$(")
	not contains(command, "`")
}

git_commit_text_command if {
	not contains(command, "$(")
	not contains(command, "`")
	not contains(command, "<<")
	regex.match(git_commit_only_pattern, unquoted_command)
}

git_commit_only_pattern := `^[[:space:]]*git([[:space:]]+-C[[:space:]]+[^[:space:];|&()<>]+)?[[:space:]]+(add|commit)[^;|&()<>]*(&&[[:space:]]*git([[:space:]]+-C[[:space:]]+[^[:space:];|&()<>]+)?[[:space:]]+(add|commit)[^;|&()<>]*)*$`

block_reason := "🧁 Cupcake blocked an er-builds-pvp.py run with no `--jobs`. Its default is cpu_count - 1 forked workers (15 here), and rankings run several at a time: on 2026-10-01 one unscoped run held 17 processes beside four other rankings, load average 33 on 16 cores. The workers already run at nice 19 with idle I/O, so the worker count is the only lever. Pass it: `--jobs 4` for a full RL window ranking, `--jobs 1` or `--jobs 2` for a `--weapon`-scoped run. `--selftest` and `--help` are exempt."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	pvp_invoked
	not has_jobs
	not non_ranking_mode
	not text_mention_only

	decision := {
		"rule_id": "ER-EFFECTS-REQUIRE-PVP-JOBS",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nSource: ", command]),
	}
}
