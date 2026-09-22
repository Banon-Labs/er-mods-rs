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
#     required_signals: ["frida_evidence"]
package cupcake.policies.claude.teardown_must_relaunch

import rego.v1

import data.cupcake.system.commands

tool_command := object.get(input.tool_input, "command", "")

# The texts this command actually hands to a shell, quoted operand spans and heredoc
# bodies anchor-neutralised. Read instead of the raw command for the reason
# `no_whole_check_sh` records at length: matching the whole command string asks whether
# two tokens are CO-PRESENT, not whether either one runs.
#
# That is not a theoretical distinction here. Measured 2026-09-21 (bd er-effects-rs-ak3q,
# bd er-effects-rs-if5l): `git commit -F - <<EOF ... EOF` was refused because the commit
# message described this guard and named the script, and `bd create --description "..."`
# was refused because the issue text did. The issue reporting the second one had to be
# filed through a body file written by a separate command in order to exist at all, and the
# commit message had to be reworded to say "a repo-relative teardown command" instead of
# naming the file. Rewording a commit message or an issue body to appease a matcher
# degrades the record this guard is not there to police, and the invoked binary in both
# cases was `git` and `bd`.
executed_texts := commands.input_executed_texts

# Whitespace-normalized, so a command written across lines matches the same way in
# the live engine (which collapses whitespace) and under `opa test` (which does not).
norm_command_for(cmd) := concat(" ", [word |
	some word in split(replace(replace(replace(cmd, "\t", " "), "\r", " "), "\n", " "), " ")
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

# Whether a token names one of the scripts this guard understands.
#
# This used to split every token and index the last path component. The live Cupcake WASM
# evaluator aborted in that helper on a long read-only Python extraction command that named no
# teardown script at all, turning a guard into a policy-engine crash. The guard only needs exact
# basename matching for two known script names, so avoid array indexing entirely.
token_names_teardown(tok) if {
	tok == "er-teardown.py"
}

token_names_teardown(tok) if {
	endswith(tok, "/er-teardown.py")
}

token_names_run_branch(tok) if {
	tok == "er-run-branch.py"
}

token_names_run_branch(tok) if {
	endswith(tok, "/er-run-branch.py")
}

# `~/Elden/launch.sh` is the user's own launcher; `er-run-gamescope.sh` calls
# `er-run-branch.py` inside gamescope's nested X server, which is what lets the game boot
# while the host Xwayland sits at its client ceiling. Added 2026-09-15, after the guard
# refused a teardown paired with a real relaunch it could not recognise -- a correct rule
# applied to a launcher that had not been told to it, which leaves the user with no game
# exactly like the case the rule exists to prevent.
token_names_user_launcher(tok) if {
	endswith(tok, "Elden/launch.sh")
}

token_names_user_launcher(tok) if {
	tok == "er-run-gamescope.sh"
}

token_names_user_launcher(tok) if {
	endswith(tok, "/er-run-gamescope.sh")
}

# A teardown that will actually kill something: the script stands in a command slot, and
# its own next word is not `--status`.
#
# Folding `--status` in here rather than testing it as a separate `status_only` closes a
# hole the two-rule shape had: `er-teardown.py --status; er-teardown.py` satisfied "some
# teardown is followed by --status" and the real kill chained behind it went unexamined.
killing_teardown if {
	some text in executed_texts
	words := commands.command_slot_words(text)
	some index, word in words
	token_names_teardown(word)
	commands.word_in_command_slot(words, index)
	next_word(words, index) != "--status"
}

# The word after `index`, or the empty string when the invocation ends the text. Spelled
# with `else` because a bare `words[index + 1]` is undefined past the end, and an undefined
# term takes the whole rule with it -- here that would mean a teardown written as the last
# word of a command was not a teardown at all.
next_word(words, index) := word if {
	index + 1 < count(words)
	word := words[index + 1]
} else := ""

# Any invocation of the teardown script, with or without a path prefix, `--status` included.
runs_teardown if {
	some text in executed_texts
	words := commands.command_slot_words(text)
	some index, word in words
	token_names_teardown(word)
	commands.word_in_command_slot(words, index)
}

invokes_run_branch if {
	some text in executed_texts
	words := commands.command_slot_words(text)
	some index, word in words
	token_names_run_branch(word)
	commands.word_in_command_slot(words, index)
}

invokes_user_launcher if {
	some text in executed_texts
	words := commands.command_slot_words(text)
	some index, word in words
	token_names_user_launcher(word)
	commands.word_in_command_slot(words, index)
}

# The relaunch that has to ride along. `er-run-branch.py` is the sanctioned
# launcher in this repo; `~/Elden/launch.sh` and `er-run-gamescope.sh` are accepted too.
relaunches if {
	invokes_run_branch
}

relaunches if {
	invokes_user_launcher
}

# A dry run stages and launches nothing, so pairing a teardown with one would
# satisfy the letter of the rule and still leave the user with no game.
#
# Asked per SEGMENT, because `--dry-run` only excuses nothing when it belongs to the launch: a
# teardown chained with a real launch and some other command's `--dry-run` is still a relaunch.
dry_run if {
	some text in executed_texts
	some segment in commands.shell_segments(text)
	contains(segment, "er-run-branch.py")
	contains(segment, "--dry-run")
}

signals := object.get(input, "signals", {})
raw_evidence := object.get(signals, "frida_evidence", "")

evidence := raw_evidence if {
	is_string(raw_evidence)
}

evidence := "" if {
	not is_string(raw_evidence)
}

measurement_proven if {
	startswith(evidence, "PROVEN")
}

# A standalone teardown is allowed only after the run has produced the measurement that justified
# having the game up. That is the workflow the live-source-edit guard demands: measure, end the
# agent-owned run, edit. Without the evidence signal this remains blocked, so a run the user is
# inspecting cannot be killed just because the command is bare.
standalone_segments_for(cmd) := split(replace(norm_command_for(cmd), "|", ";"), ";")

teardown_alone_after_measurement if {
	measurement_proven
	segments := [segment |
		some segment in standalone_segments_for(tool_command())
		norm_command_for(segment) != ""
	]
	non_cd := [segment |
		some segment in segments
		not cd_segment(segment)
	]
	count(non_cd) == 1
	teardown_segment_alone(non_cd[0])
}

cd_segment(segment) if {
	words := [tok |
		some tok in split(norm_command_for(segment), " ")
		tok != ""
	]
	count(words) == 2
	words[0] == "cd"
}

# Asked of one segment, which is what survived the two rewrites this rule was caught between.
# `main` sharpened the old whole-command `teardown_alone` to open on `runs_teardown`, so a script
# named only in a commit message or an issue body could not satisfy it. That sharpening now lives
# one level up, in the `killing_teardown` this policy denies on, so the exception never sees a
# command whose teardown is prose. What is left for the exception to decide is narrower, and
# unchanged by either side: that this segment's only unexcused word is the script itself.
teardown_segment_alone(segment) if {
	contains(segment, "er-teardown.py")
	words := [tok |
		some tok in split(norm_command_for(segment), " ")
		tok != ""
	]
	rest := [words[i] |
		some i, _ in words
		not cd_word(words[i])
		not cd_value_at(words, i)
		not interpreter_word(words[i])
		not redirect_word(words[i])
		not reason_word(words, words[i])
		not reason_value_at(words, i)
	]
	count(rest) == 1
	token_names_teardown(rest[0])
}

cd_word(tok) if {
	tok == "cd"
}

cd_value_at(words, i) if {
	i > 0
	words[i - 1] == "cd"
}

interpreter_word(tok) if {
	tok in {"python", "python3"}
}

redirect_word(tok) if {
	contains(tok, ">")
}

redirect_word(tok) if {
	startswith(tok, "/dev/")
}

reason_word(_, tok) if {
	tok == "--reason"
}

reason_word(_, tok) if {
	startswith(tok, "--reason=")
}

reason_value_at(words, i) if {
	i > 0
	words[i - 1] == "--reason"
}

block_reason := "🧁 Cupcake blocked a teardown that does not relaunch. `scripts/er-teardown.py` belongs immediately before a launch and nowhere else -- stapled to the front of a build it reads as hygiene and is actually a kill. On 2026-09-12 exactly that ended run br-20260912-204637-08ba while the user was driving it, one line after the run logged the fix they were inspecting. Put the launch in the SAME command:\n\n    python3 scripts/er-teardown.py > /dev/null 2>&1; python3 scripts/er-run-branch.py --with <pkg> ...\n\nBuild FIRST, in its own command, then tear down and relaunch together -- the build does not need the game stopped. `--status` is read-only and always allowed. A `--dry-run` launch does not count: it stages nothing and still leaves the user with no game."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	killing_teardown
	not teardown_alone_after_measurement
	not relaunches

	decision := {
		"rule_id": "ER-EFFECTS-TEARDOWN-MUST-RELAUNCH",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nSource: ", tool_command()]),
	}
}

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	killing_teardown
	relaunches
	dry_run

	decision := {
		"rule_id": "ER-EFFECTS-TEARDOWN-MUST-RELAUNCH",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nSource: ", tool_command()]),
	}
}
