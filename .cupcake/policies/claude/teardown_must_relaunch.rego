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
#     asked for yet. `--status`, `--help` and `-h` are read-only and always allowed.
#
#     The rule is deliberately not "ask whether a run is live". The agent cannot
#     see whether a person is looking at the screen, and a memory saying "never
#     tear down a run the user is touching" did not stop it. Requiring the launch
#     in the same command makes the failure mode unreachable: the worst outcome
#     becomes a restarted game rather than no game.
#
#     Four standalone forms are excused: one after a proven measurement, one whose
#     `--reason=invalid-run-<token>` declares the run invalid, which AGENTS.md obliges the
#     agent to stop, one whose `--reason=stamped-<token>` ends the run that stamped the
#     commit its branch is about to push, once the `stamped_run` signal proves the stamp landed,
#     and one whose `--reason=push-<token>` follows the user's own `authorize <sha>` reply.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
#     required_signals: ["frida_evidence", "stamped_run", "push_teardown"]
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
	not next_word(words, index) in read_only_flags
}

# Flags that make the script kill nothing. `--status` reports; `--help`/`-h` are argparse's,
# which prints usage and exits inside `parse_args()` before any teardown code runs.
read_only_flags := {"--status", "--help", "-h"}

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

# A standalone teardown that declares the run invalid needs no measurement. AGENTS.md obliges the
# agent to stop an agent-launched run once structured evidence shows a required patch did not
# apply, the wrong build is loaded, or the test state cannot answer the user's question -- and to
# complete that stop before continuing implementation. Such a run has by definition produced no
# measurement, so the evidence gate above can never open for it. Refused 2026-10-07 on
# `er-teardown.py --reason=build-import-did-not-apply 2>&1 | tail -3`: the `build_url` import never
# applied, the character was still the Vagabond at level 9, and the guard kept the invalid run up.
#
# The declaration is a fixed spelling, `--reason=invalid-run-<one-token>`, so it is written on
# purpose and lands in `er-run-outcome.txt` as such. Everything else stays as narrow as the
# measured form: one teardown segment, no chained work, a one-token reason. The single extra
# admission is an output trim (`| tail -<n>` / `| head -<n>`), which reads the teardown's own
# output and does no work.
invalid_run_reason_prefix := "--reason=invalid-run-"

teardown_alone_declaring_invalid_run if {
	teardown_alone_declaring(invalid_run_reason_prefix)
}

# A standalone teardown that ends the run which stamped the commit about to be pushed. AGENTS.md
# says to close Elden Ring before pushing, because `scripts/check.sh`, which the pre-push hook runs,
# exits 2 while `er-teardown.py --status` reports a game; and it says a push of crate code waits
# for a run of that exact tip. So the agent launches a run to stamp the commit, and once the stamp
# is in, that run has to end before the push. It is neither a measurement nor an invalid run, so
# neither form above could admit it. Refused 2026-10-22 on
# `er-teardown.py --reason=push-needs-game-closed`, after run br-20261022-023259-d732 had loaded
# er_quickload.dll built at a9be7625, `check-runtime-evidence.sh` exited 0 for that tip, and the
# user had authorized the push; the teardown had to be handed to the user in a kitty tab.
#
# The declaration is a fixed spelling, `--reason=stamped-<one-token>`, with the same shape rules
# as the invalid-run form. The declaration alone admits nothing: the `stamped_run` signal has to
# read `PROVEN`, which `scripts/cupcake_stamped_run.py` prints only when the newest `br-*` run is
# up (its `run.json` is there and its launcher is alive), it was launched on a branch some checkout
# holds, its own log carries a `build git=` stamp on that branch, and that checkout's
# `scripts/check-runtime-evidence.sh` passes for the tip on this run's evidence. Anything else,
# including a signal that crashed or timed out, leaves the form refused.
stamped_reason_prefix := "--reason=stamped-"

raw_stamped_run := object.get(signals, "stamped_run", "")

stamped_run := raw_stamped_run if {
	is_string(raw_stamped_run)
}

stamped_run := "" if {
	not is_string(raw_stamped_run)
}

stamp_proven if {
	startswith(stamped_run, "PROVEN ")
}

teardown_alone_after_stamping_run if {
	stamp_proven
	teardown_alone_declaring(stamped_reason_prefix)
}

# A standalone teardown the user's push authorization pays for. User directive 2026-10-22,
# verbatim: "to authorize a push sha is to authorize a teardown". The pre-push hook cannot run
# while the game is up, so a push the user authorized needs the game closed first, whatever the
# run was for. That day the run up was testing an uncommitted build, so it stamped nothing and the
# `stamped-` form could not admit it; `--reason=cursor-test-done-for-push` was refused and the
# authorization went stale while the agent asked the user to close the game.
#
# The declaration is `--reason=push-<one-token>`, with the same shape rules as the forms above.
# It admits nothing alone: the `push_teardown` signal has to read `AUTHORIZED`, which
# `scripts/cupcake_push_teardown.py` prints only when the user's last message is exactly
# `authorize <sha>`, the sha is a pushable local branch tip, and that reply has not paid for a push.
push_reason_prefix := "--reason=push-"

raw_push_teardown := object.get(signals, "push_teardown", "")

push_teardown := raw_push_teardown if {
	is_string(raw_push_teardown)
}

push_teardown := "" if {
	not is_string(raw_push_teardown)
}

teardown_alone_for_authorized_push if {
	startswith(push_teardown, "AUTHORIZED\t")
	teardown_alone_declaring(push_reason_prefix)
}

push_teardown_note := concat("", ["\n\n`push_teardown` signal: ", push_teardown]) if {
	push_teardown != ""
} else := ""

# The whole command is one teardown segment declaring `prefix`, optionally behind a `cd` and
# followed by an output trim.
teardown_alone_declaring(prefix) if {
	segments := [segment |
		some segment in standalone_segments_for(tool_command())
		norm_command_for(segment) != ""
	]
	non_cd := [segment |
		some segment in segments
		not cd_segment(segment)
		not output_trim_segment(segment)
	]
	count(non_cd) == 1
	teardown_segment_alone(non_cd[0])
	declares_reason(non_cd[0], prefix)
}

declares_reason(segment, prefix) if {
	some tok in split(norm_command_for(segment), " ")
	startswith(tok, prefix)
	count(tok) > count(prefix)
}

# What the signal answered, for the denial. It prints only for a command naming the stamped form,
# so this is empty for every other teardown.
stamped_run_note := concat("", ["\n\n`stamped_run` signal: ", stamped_run]) if {
	stamped_run != ""
} else := ""

# `tail -3`, `head -15`: exactly two words, the second a dash and digits. Spelled without a regex
# for the engine-crash reason recorded at the top of this file.
output_trim_segment(segment) if {
	words := [tok |
		some tok in split(norm_command_for(segment), " ")
		tok != ""
	]
	count(words) == 2
	words[0] in {"tail", "head"}
	startswith(words[1], "-")
	count(words[1]) > 1
	trim(substring(words[1], 1, -1), "0123456789") == ""
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

block_reason := "🧁 Cupcake blocked a teardown that does not relaunch. `scripts/er-teardown.py` belongs immediately before a launch and nowhere else -- stapled to the front of a build it reads as hygiene and is actually a kill. On 2026-09-12 exactly that ended run br-20260912-204637-08ba while the user was driving it, one line after the run logged the fix they were inspecting. Put the launch in the SAME command:\n\n    python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py > /dev/null 2>&1; python3 /home/banon/projects/er-mods-rs/scripts/er-run-branch.py --with <pkg> ...\n\nBuild FIRST, in its own command, then tear down and relaunch together -- the build does not need the game stopped. `--status`, `--help` and `-h` are read-only and always allowed. A `--dry-run` launch does not count: it stages nothing and still leaves the user with no game.\n\nA teardown that is the WHOLE command is allowed once this run has produced the measurement that justified having the game up -- the `frida_evidence` signal has to read `PROVEN`, the same evidence the live-source-edit guard demands before an edit. That is also the form to hand the user when they ask for one:\n\n    python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=<one-token-why>\n\nA run that has become INVALID needs no measurement, and stopping it is an obligation, not an option: AGENTS.md (\"Elden Ring Runtime Probe Hygiene\") says that when structured evidence shows a required DLL/runtime patch did not apply, the wrong build is loaded, or the test state cannot answer the user's question, the agent must stop the agent-launched run before continuing implementation. Declare it in the reason, as the whole command (a trailing `| tail -<n>` or `| head -<n>` is allowed):\n\n    python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=invalid-run-<one-token-why>\n\nA run launched to stamp a commit for its push has to end before that push, because `scripts/check.sh`, which the pre-push hook runs, refuses while the game is up. Once its stamp is in, end it as the whole command, with the same trim allowed:\n\n    python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=stamped-<one-token-why>\n\nThat form needs the `stamped_run` signal to read `PROVEN`: the newest `br-*` run is up, it was launched on a branch some checkout holds, its own log opens with a `build git=` line on that branch, and that checkout's `scripts/check-runtime-evidence.sh` passes for the tip on this run's evidence. Its answer is quoted below when it was asked.\n\nA push the user authorized needs the game closed too, whatever the run was for. When the user's last message is exactly `authorize <sha>` for a branch tip whose push is still waiting, end it as the whole command, with the same trim allowed:\n\n    python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=push-<one-token-why>\n\nThat form needs the `push_teardown` signal to read `AUTHORIZED`.\n\nSpell it absolutely there. The user's shell is not in the repo root, so `python3 scripts/er-teardown.py` names nothing for them. `--reason` takes ONE token: a quoted multi-word value is trailing work to this guard and is denied."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	killing_teardown
	not teardown_alone_after_measurement
	not teardown_alone_declaring_invalid_run
	not teardown_alone_after_stamping_run
	not teardown_alone_for_authorized_push
	not relaunches

	decision := {
		"rule_id": "ER-EFFECTS-TEARDOWN-MUST-RELAUNCH",
		"severity": "HIGH",
		"reason": concat("", [block_reason, stamped_run_note, push_teardown_note, "\n\nSource: ", tool_command()]),
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
