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
#     required_tools: ["Bash"]
package cupcake.policies.claude.teardown_must_relaunch

import rego.v1

command := object.get(input.tool_input, "command", "")

# Whitespace-normalized, so a command written across lines matches the same way in
# the live engine (which collapses whitespace) and under `opa test` (which does not).
norm_command := concat(" ", [word |
	some word in split(replace(replace(replace(command, "\t", " "), "\r", " "), "\n", " "), " ")
	word != ""
])

# Any invocation of the teardown script, with or without a path prefix.
runs_teardown if {
	regex.match("(^|[[:space:];|&('\"`])([^[:space:];|&]*/)?er-teardown\\.py($|[^[:alnum:]_.-])", norm_command)
}

# Read-only: reports what is running and kills nothing.
status_only if {
	regex.match(`er-teardown\.py[[:space:]]+--status($|[[:space:]])`, norm_command)
}

# The relaunch that has to ride along. `er-run-branch.py` is the only sanctioned
# launcher in this repo; `~/Elden/launch.sh` is the user's own and is accepted too.
relaunches if {
	regex.match("(^|[[:space:];|&('\"`])([^[:space:];|&]*/)?er-run-branch\\.py($|[^[:alnum:]_.-])", norm_command)
}

relaunches if {
	contains(norm_command, "Elden/launch.sh")
}

# A dry run stages and launches nothing, so pairing a teardown with one would
# satisfy the letter of the rule and still leave the user with no game.
dry_run if {
	regex.match(`er-run-branch\.py[^;|&]*--dry-run($|[[:space:]])`, norm_command)
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
teardown_alone if {
	regex.match(`^[[:space:]]*(python3?[[:space:]]+)?[^[:space:];|&]*er-teardown\.py([[:space:]]*[0-9]?>&[0-9]|[[:space:]]*[0-9]?>[[:space:]]*[^[:space:];|&<>]+)*[[:space:]]*$`, norm_command)
}

block_reason := "🧁 Cupcake blocked a teardown that does not relaunch. `scripts/er-teardown.py` belongs immediately before a launch and nowhere else -- stapled to the front of a build it reads as hygiene and is actually a kill. On 2026-09-12 exactly that ended run br-20260912-204637-08ba while the user was driving it, one line after the run logged the fix they were inspecting. Put the launch in the SAME command:\n\n    python3 scripts/er-teardown.py > /dev/null 2>&1; python3 scripts/er-run-branch.py --with <pkg> ...\n\nBuild FIRST, in its own command, then tear down and relaunch together -- the build does not need the game stopped. `--status` is read-only and always allowed. A `--dry-run` launch does not count: it stages nothing and still leaves the user with no game."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
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
	input.tool_name == "Bash"
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
