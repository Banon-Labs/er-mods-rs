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

tool_command := object.get(input.tool_input, "command", "")

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

# Separators become spaces too, so a token that ends a statement (`er-teardown.py;`) is the same
# token as one that does not, and a quoted or parenthesised invocation still tokenises.
tokens_for(cmd) := [tok |
	separated := replace(
		replace(
			replace(
				replace(
					replace(
						replace(
							replace(
								replace(
									norm_command_for(cmd),
									";", " ",
								),
								"|", " ",
							),
							"&", " ",
						),
						"(", " ",
					),
					")", " ",
				),
				"`", " ",
			),
			"'", " ",
		),
		`"`, " ",
	)
	some tok in split(separated, " ")
	tok != ""
]

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

invokes_teardown if {
	cmd := tool_command()
	contains(cmd, "er-teardown.py")
	some tok in tokens_for(cmd)
	token_names_teardown(tok)
}

invokes_run_branch if {
	cmd := tool_command()
	contains(cmd, "er-run-branch.py")
	some tok in tokens_for(cmd)
	token_names_run_branch(tok)
}

# Any invocation of the teardown script, with or without a path prefix.
runs_teardown if {
	invokes_teardown
}

# Read-only: reports what is running and kills nothing. `--status` has to be the teardown's own
# next word, not merely present somewhere in the command.
status_only if {
	cmd := tool_command()
	contains(cmd, "er-teardown.py")
	tokens := tokens_for(cmd)
	some i
	token_names_teardown(tokens[i])
	tokens[i + 1] == "--status"
}

# The relaunch that has to ride along. `er-run-branch.py` is the sanctioned
# launcher in this repo; `~/Elden/launch.sh` is the user's own and is accepted too.
relaunches if {
	invokes_run_branch
}

relaunches if {
	contains(norm_command_for(tool_command()), "Elden/launch.sh")
}

# `er-run-gamescope.sh` is a launcher too: it calls `er-run-branch.py` inside gamescope's nested
# X server, which is what lets the game boot while the host Xwayland sits at its client ceiling.
# Added 2026-09-15, after the guard refused a teardown paired with a real relaunch it could not
# recognise -- a correct rule applied to a launcher that had not been told to it, which leaves
# the user with no game exactly like the case the rule exists to prevent.
relaunches if {
	contains(norm_command_for(tool_command()), "er-run-gamescope.sh")
}

# A dry run stages and launches nothing, so pairing a teardown with one would
# satisfy the letter of the rule and still leave the user with no game.
#
# Asked per SEGMENT, because `--dry-run` only excuses nothing when it belongs to the launch: a
# teardown chained with a real launch and some other command's `--dry-run` is still a relaunch.
command_segments_for(cmd) := split(replace(replace(norm_command_for(cmd), "|", ";"), "&", ";"), ";")

dry_run if {
	cmd := tool_command()
	contains(cmd, "er-run-branch.py")
	contains(cmd, "--dry-run")
	some segment in command_segments_for(cmd)
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
# Read off `norm_command_for(tool_command())`, which is split on whitespace ONLY. That is what makes the
# rule correct: a separator stays glued to its token, so `er-teardown.py;` is not the bare script
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

# `--reason <why>`, which records why the run ended in the run's own outcome record.
#
# Allowed on a standalone teardown because it adds evidence, not work. Without it the only
# expressible form of "this run is finished" was the one that says nothing about why, so the
# outcome line read `reason=agent-teardown` for every deliberate ending -- and this guard was
# teaching the agent to drop the flag to get past it. Both spellings, since `--reason=why` is one
# token and `--reason why` is two.
reason_word(_, tok) if {
	tok == "--reason"
}

reason_word(_, tok) if {
	startswith(tok, "--reason=")
}

# The value that follows a separate `--reason`. Indexed rather than matched, because the value is
# arbitrary text and nothing about the word itself says it belongs to the flag.
reason_value_at(words, i) if {
	i > 0
	words[i - 1] == "--reason"
}

teardown_alone if {
	cmd := tool_command()
	contains(cmd, "er-teardown.py")
	words := [tok |
		some tok in split(norm_command_for(cmd), " ")
		tok != ""
	]
	rest := [words[i] |
		some i, _ in words
		not interpreter_word(words[i])
		not redirect_word(words[i])
		not reason_word(words, words[i])
		not reason_value_at(words, i)
	]
	count(rest) == 1
	token_names_teardown(rest[0])
}

block_reason := "🧁 Cupcake blocked a teardown that does not relaunch. `scripts/er-teardown.py` belongs immediately before a launch and nowhere else -- stapled to the front of a build it reads as hygiene and is actually a kill. On 2026-09-12 exactly that ended run br-20260912-204637-08ba while the user was driving it, one line after the run logged the fix they were inspecting. Put the launch in the SAME command:\n\n    python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py > /dev/null 2>&1; python3 /home/banon/projects/er-mods-rs/scripts/er-run-branch.py --with <pkg> ...\n\nBuild FIRST, in its own command, then tear down and relaunch together -- the build does not need the game stopped. `--status` is read-only and always allowed. A `--dry-run` launch does not count: it stages nothing and still leaves the user with no game.\n\nA teardown that is the WHOLE command is allowed, which is the form to hand the user when they ask for one:\n\n    python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=<one-token-why>\n\nSpell it absolutely there. The user's shell is not in the repo root, so `python3 scripts/er-teardown.py` names nothing for them. `--reason` takes ONE token: a quoted multi-word value is trailing work to this guard and is denied."

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
		"reason": concat("", [block_reason, "\n\nSource: ", tool_command()]),
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
		"reason": concat("", [block_reason, "\n\nSource: ", tool_command()]),
	}
}
