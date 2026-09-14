# METADATA
# scope: package
# title: A teardown must relaunch in the same command
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-TEARDOWN-MUST-RELAUNCH
#   description: >-
#     Hard block on a Bash command that runs `scripts/er-teardown.py` without
#     launching again in the same command. Teardown belongs immediately before a
#     launch and nowhere else. Stapled to the front of a build it reads as launch
#     hygiene and is actually a kill: on 2026-09-12 it ended run
#     br-20260912-204637-08ba while the user was driving it, one line after that
#     run logged the placement fix they were inspecting, for a rebuild nobody had
#     asked for yet. `--status` is read-only and always allowed.
#
#     The rule is deliberately not "ask whether a run is live". The agent cannot
#     see whether a person is looking at the screen, and a memory saying "never
#     tear down a run the user is touching" did not stop it. Requiring the launch
#     in the same command makes the failure mode unreachable: the worst outcome
#     becomes a restarted game rather than no game.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash", "bash"]
package cupcake.policies.claude.teardown_must_relaunch

import rego.v1

import data.cupcake.system.commands

command := object.get(input.tool_input, "command", "")

# Whitespace-normalized, so a command written across lines matches the same way in
# the live engine (which collapses whitespace) and under `opa test` (which does not).
norm_command := concat(" ", [word |
	some word in split(replace(replace(replace(command, "\t", " "), "\r", " "), "\n", " "), " ")
	word != ""
])

# Every rule below asks a question about TOKENS -- is this script invoked, is the next word
# `--status`, is the whole command just the teardown -- and answers it without `regex.match`.
# That is forced, not stylistic.
#
# All four were first written as regexes. They pass `opa test`, whose Go runtime links RE2
# natively, and they CRASHED the engine: every `cupcake eval` on this harness aborted with
#
#     memory fault at wasm address 0x1d24804 in linear memory of size 0x1c0000:
#     wasm trap: out of bounds memory access
#
# the backtrace running `re2::Regexp::Decref` <- `re2::RE2::~RE2` <- `reuse(re2::RE2*)` <-
# `opa_regex_match` <- a rule in this file. `reuse` is OPA's compiled-regex cache evicting an
# entry, and the faulting pointer is about 16x past the end of a 1.75 MB linear memory, so the
# cache hands back freed memory. The subject string was 80 characters long, and removing one
# pattern moved the fault to the next rule rather than curing it: the rulebook's policies together
# compile more distinct regexes than that cache survives, and these were the ones that tipped it.
#
# A crashed evaluation is not a refusal. Cupcake returns `{}` and every policy in the rulebook
# goes silent at once, so a regex added here can disarm the Elden Ring launch guard. Prefer
# `split`/`contains`/`startswith` in new policies, and treat a green `opa test` as necessary and
# never as evidence -- `scripts/test-cupcake-policies.py` drives the real runtime and is what
# caught this.

# Separators become spaces too, so a token that ends a statement (`er-teardown.py;`) is the same
# token as one that does not, and a quoted or parenthesised invocation still tokenises.
separated := replace(replace(replace(replace(replace(replace(replace(replace(
	norm_command,
	";", " "), "|", " "), "&", " "), "(", " "), ")", " "), "`", " "), "'", " "), `"`, " ")

tokens := [tok |
	some tok in split(separated, " ")
	tok != ""
]

# The last path component of a token, so `scripts/er-teardown.py` and
# `/home/x/repo/scripts/er-teardown.py` are both the teardown while `er-teardown-report.py` --
# a different script whose name merely starts the same way -- is not.
script_name(tok) := parts[count(parts) - 1] if {
	parts := split(tok, "/")
}

invokes(script) if {
	some tok in tokens
	script_name(tok) == script
}

# Any invocation of the teardown script, with or without a path prefix.
runs_teardown if {
	invokes("er-teardown.py")
}

# Read-only: reports what is running and kills nothing. `--status` has to be the teardown's own
# next word, not merely present somewhere in the command.
status_only if {
	some i
	script_name(tokens[i]) == "er-teardown.py"
	tokens[i + 1] == "--status"
}

# The relaunch that has to ride along. `er-run-branch.py` is the only sanctioned
# launcher in this repo; `~/Elden/launch.sh` is the user's own and is accepted too.
relaunches if {
	invokes("er-run-branch.py")
}

relaunches if {
	contains(norm_command, "Elden/launch.sh")
}

# A dry run stages and launches nothing, so pairing a teardown with one would
# satisfy the letter of the rule and still leave the user with no game.
#
# Asked per SEGMENT, because `--dry-run` only excuses nothing when it belongs to the launch: a
# teardown chained with a real launch and some other command's `--dry-run` is still a relaunch.
command_segments := split(replace(replace(norm_command, "|", ";"), "&", ";"), ";")

dry_run if {
	some segment in command_segments
	contains(segment, "er-run-branch.py")
	contains(segment, "--dry-run")
}

# A teardown that is the WHOLE command, ending a run on purpose.
#
# The harm this rule exists to stop was a teardown that rode along with OTHER work
# -- `er-teardown.py; er-build-dlls.sh ...` read as launch hygiene and was a kill.
# A teardown alone is the honest form of "this run is finished", and forbidding it
# deadlocked the very workflow the sibling guard demands: a source edit is refused
# while a run is live, so ending that run has to be expressible without also
# relaunching a build that is about to be replaced.
#
# `>/dev/null`-style redirections are allowed because they suppress output rather
# than add work; a `;`, `|`, `&` or `&&` that introduces another command is not.
#
# Read off `norm_command`, which is split on whitespace ONLY. That is what makes the rule
# correct: a separator stays glued to its token, so `er-teardown.py;` is not the bare script
# name and a chained command cannot pass as one that stands alone.
interpreter_word(tok) if {
	tok in {"python", "python3"}
}

# `>`, `2>&1`, `>/dev/null` and the `/dev/null` operand of a detached `>`.
redirect_word(tok) if {
	contains(tok, ">")
}

redirect_word(tok) if {
	startswith(tok, "/dev/")
}

teardown_alone if {
	rest := [tok |
		some tok in split(norm_command, " ")
		tok != ""
		not interpreter_word(tok)
		not redirect_word(tok)
	]
	count(rest) == 1
	script_name(rest[0]) == "er-teardown.py"
}

block_reason := "🧁 Cupcake blocked a teardown that does not relaunch. `scripts/er-teardown.py` belongs immediately before a launch and nowhere else -- stapled to the front of a build it reads as hygiene and is actually a kill. On 2026-09-12 exactly that ended run br-20260912-204637-08ba while the user was driving it, one line after the run logged the fix they were inspecting. Put the launch in the SAME command:\n\n    python3 scripts/er-teardown.py > /dev/null 2>&1; python3 scripts/er-run-branch.py --with <pkg> ...\n\nBuild FIRST, in its own command, then tear down and relaunch together -- the build does not need the game stopped. `--status` is read-only and always allowed. A `--dry-run` launch does not count: it stages nothing and still leaves the user with no game."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	commands.is_tool(input, "Bash")
	runs_teardown
	not status_only
	not teardown_alone
	not relaunches

	decision := {
		"rule_id": "ER-EFFECTS-TEARDOWN-MUST-RELAUNCH",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nSource: ", command]),
	}
}

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	commands.is_tool(input, "Bash")
	runs_teardown
	not status_only
	relaunches
	dry_run

	decision := {
		"rule_id": "ER-EFFECTS-TEARDOWN-MUST-RELAUNCH",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nSource: ", command]),
	}
}
