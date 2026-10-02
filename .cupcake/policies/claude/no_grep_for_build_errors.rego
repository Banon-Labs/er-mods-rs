# METADATA
# scope: package
# title: A build's exit code is the verdict; grepping its output for "error" is not
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-GREP-FOR-BUILD-ERRORS
#   description: >-
#     Hard block on piping a BUILD/TEST/CHECK command into a pattern matcher in order to decide
#     whether it succeeded. The tool already answers that question exactly, once, at the end: its
#     EXIT CODE. A grep over its output is a strictly worse oracle -- it is a guess at which strings
#     a failure prints -- and unlike the exit code it can be wrong in the direction that matters,
#     reporting success for a build that failed.
#
#     MEASURED 2026-09-03, and this is the whole reason the policy exists. `cargo check -p
#     er-quickload` was run as `... 2>&1 | grep -E 'error' -A6 | head -20` followed by `echo "---
#     clean ---"`. The build had FAILED with E0603 (a private module path), but the matcher's window
#     did not surface it, "--- clean ---" printed, and the failure was reported to the user as a
#     successful build. The DLL on disk stayed at the previous link for another twenty minutes of
#     work built on top of it. The user's response is the rule: "When you run something that reports
#     error codes, why would you grep it for errors? It has an error code. It only returns one at
#     the end." There was no defensible answer, so the behaviour is prevented rather than
#     remembered -- an advisory note would only fire if it were recalled at the right moment, and
#     this one would not have been.
#
#     WHAT IS BLOCKED: a build-ish command (a compiling/verdict cargo subcommand -- build, check,
#     clippy, test, bench, doc, run, nextest, rustc, miri, fix, fmt, install, the built-in aliases
#     b/c/t/r/d, `cargo xwin ...` --
#     or rustc, opa, make, ninja, cmake, go, npm/pnpm/yarn, pytest, tsc, or one of this repo's own
#     build scripts), standing in COMMAND POSITION of a pipeline stage, whose output reaches
#     grep/rg/egrep/fgrep/ag/ack running in command position of a LATER stage of the same pipeline.
#     WHAT IS NOT: piping into head/tail/sed/awk/wc/jq/sort/uniq/cut/tr/less/python, which shape or
#     excerpt output rather than adjudicating it; grep ANYWHERE else, including over a build LOG FILE
#     already on disk, which is reading evidence after the exit code has already been believed; a
#     build word that is only an ARGUMENT or PATTERN of another program (`pgrep -f 'cargo|rustc' |
#     grep -v pgrep`, `ps aux | grep cargo`); informational cargo (`cargo tree | grep serde`); and
#     any pipeline whose matcher is not deciding pass/fail because the exit code was captured first.
#
#     THE HAPPY PATH is simply to run the command and let a non-zero exit speak, then read the tail
#     for the message -- `cmd; echo "exit=$?"`, or `cmd || tail -40 build.log`. For a background
#     build, note that the harness's task exit code describes the WRAPPER, not the build: read the
#     log file the build wrote, or its provenance record.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.no_grep_for_build_errors

import rego.v1

import data.cupcake.system.commands

# ---------------------------------------------------------------------------
# A PIPE FROM THE BUILD INTO THE MATCHER, NOT CO-PRESENCE (2026-09-26)
#
# The first version asked two whole-command questions -- does a build word follow any separator
# or quote, and does `| grep` appear anywhere -- and denied when both held. That is co-presence,
# not a pipe from one into the other, and on 2026-09-26 it denied a read-only process listing run
# before a disk cleanup deleted Rust target/ dirs:
#
#   pgrep -a -f 'cargo|rustc' | grep -v pgrep | cut -c1-200 | head; ...
#
# `cargo` there is a pattern handed to pgrep (the quote in front of it counted as a separator) and
# the grep filters pgrep's process list. No build ran. Same class: `ps aux | grep cargo`,
# `ls ~/.cargo/bin | grep x`, and `cargo build -p x; ls target | grep dll`, whose grep reads ls.
#
# The question is now structural, and it is answered in tokens, not new patterns: a regex added to
# the rulebook is a hazard to every policy at once (bd
# a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14, recorded in
# teardown_must_relaunch.rego), and this rewrite retires the two this file used to compile.
#
#   1. Each executed text -- commands.input_executed_texts: quoted operands neutralised, so the
#      `|` in `'cargo|rustc'` is no pipe, and `bash -c '...'` payloads decomposed into texts of
#      their own -- is cut into statements at `;`, `&&`, `||`, `&` and newlines, pipelines whole.
#   2. Each statement is cut at its pipes, in order.
#   3. A stage is a BUILD when a build tool stands in command position in it -- behind nothing but
#      wrappers (`timeout 28`, `env`, `VAR=x`, `bash`, `python3 -m`, `if`, `!`, ...). `cargo` counts
#      only with a verdict subcommand after it: `cargo tree | grep serde`, `cargo metadata | grep`
#      and `cargo --version | grep` read information, not a pass/fail. A stage is a MATCHER when
#      grep/egrep/fgrep/rg/ag/ack stands in command position in it.
#   4. Deny when a build stage feeds a matcher stage later in the same pipeline. `tee` in between
#      launders nothing: `cargo build 2>&1 | tee log | grep error` and `cargo build |& grep -c
#      error` are the same mistake with more plumbing.
#
# One coarser rule keeps compound commands covered. shell_statements cuts at every `;`, including
# the ones inside `(cargo build; echo done) | grep error`, `{ cargo build; } 2>&1 | grep error` and
# `for c in a b; do cargo test -p $c; done 2>&1 | grep FAILED`, which leaves the pipe in a
# statement that opens with the compound's close. When a stage like that feeds a matcher and a
# build stands in command position anywhere in the same command, that denies -- the old
# co-presence reading, kept only for this shape and only in the deny direction.
#
# Known reach limit, shared with every command_slot_words reader: a wrapper that takes its own
# operand (`sudo -u root cargo build`, `env -C dir cargo build`) hides the build behind a word the
# wrapper set cannot classify.
# ---------------------------------------------------------------------------

executed_texts := commands.input_executed_texts

# `&` spelled inside a redirection is not "run in the background", but shell_statements cuts at
# every `&`: without this, `cargo check 2>&1 | grep error` falls apart into `cargo check 2>` and
# `1 | grep error`, and the pipe between the build and the matcher is lost. `|&` is bash's
# `2>&1 |`, so it becomes a plain pipe.
pipeline_text(text) := replace(replace(replace(replace(text, "|&", "|"), ">&", "> "), "<&", "< "), "&>", " >")

# Every pipeline of every executed text, as its stages in order.
pipelines contains stages if {
	some text in executed_texts
	some statement in commands.shell_statements(pipeline_text(text))
	stages := split(statement, "|")
}

greps_a_build if {
	some stages in pipelines
	some build_at, stage in stages
	build_stage(stage)
	some match_at, later in stages
	match_at > build_at
	matcher_stage(later)
}

greps_a_build if {
	some stages in pipelines
	some close_at, stage in stages
	closes_compound(stage)
	some match_at, later in stages
	match_at > close_at
	matcher_stage(later)
	build_anywhere
}

build_anywhere if {
	some stages in pipelines
	some stage in stages
	build_stage(stage)
}

# A stage opening with the close of a compound command whose inside shell_statements cut apart:
# more `)` than `(` (a balanced `$(...)` in the stage is not a close), or a `}`/`done`/`fi`/`esac`
# keyword as its first word.
closes_compound(stage) if count(split(stage, ")")) > count(split(stage, "("))

closes_compound(stage) if {
	some words in stage_pieces(stage)
	words[0] in {"}", "done", "fi", "esac"}
}

# One stage as its commands, each a word list. `(` and backticks open a command of their own, so
# `(cargo build ...)` and `$(cargo build ...)` are each read from their first word. A quoted `(` or
# `|` never reaches here because executed_texts has already blanked it -- except in the raw text
# commands.rego keeps when command substitution or unbalanced quotes defeat its quote reading (see
# its LIMITS note), where quoted separators split like real ones.
stage_pieces(stage) := [words |
	some piece in split(replace(stage, "`", "("), "(")
	words := [word |
		some word in split(replace(piece, "\t", " "), " ")
		word != ""
	]
	count(words) > 0
]

# Commands whose exit code IS the verdict. `mycargo` / `not-make` never match: the whole word, or
# its last path component, has to be the tool.
build_tools := {"rustc", "opa", "make", "ninja", "cmake", "go", "npm", "pnpm", "yarn", "pytest", "tsc"}

# Including cargo's built-in aliases -- `cargo c` is `cargo check`, `b` build, `t` test, `r` run,
# `d` doc -- which are the same verdict in fewer keystrokes.
cargo_verdict_subcommands := {
	"build", "check", "clippy", "test", "bench", "doc", "run", "nextest",
	"rustc", "miri", "fix", "fmt", "install",
	"b", "c", "t", "r", "d",
}

# Matchers that ADJUDICATE. head/tail/sed/awk/wc/jq/python are deliberately absent: they excerpt or
# reshape, they do not decide whether the build passed.
matcher_tools := {"grep", "egrep", "fgrep", "rg", "ag", "ack"}

build_stage(stage) if {
	some words in stage_pieces(stage)
	some index, word in words
	build_word(words, index, word)
	in_command_slot(words, index)
}

matcher_stage(stage) if {
	some words in stage_pieces(stage)
	some index, word in words
	some tool in matcher_tools
	names_program(word, tool)
	in_command_slot(words, index)
}

build_word(_, _, word) if {
	some tool in build_tools
	names_program(word, tool)
}

# `cargo xwin build` is covered too: `build` is a later word of the same command.
build_word(words, index, word) if {
	names_program(word, "cargo")
	some later, arg in words
	later > index
	arg in cargo_verdict_subcommands
}

build_word(_, _, word) if repo_build_script(word)

names_program(word, tool) if word == tool

names_program(word, tool) if endswith(word, concat("", ["/", tool]))

# This repo's own build/check/test wrappers (er-build-dlls.sh, check.sh, check-rust-build.sh,
# act-check.sh, test-cupcake-policies.py, ...), which are `set -e` and propagate: a script under a
# `scripts/` path component whose name is that verb -- the first or last `-`/`_` word of its
# basename, after an `er-` prefix.
#
# Not a substring (2026-09-29). `contains(tail, "build")` read `scripts/er-builds-pvp.py` -- a
# character-build catalog report, "build" the noun -- as a build, and denied `for ...; do timeout 14
# python3 scripts/er-builds-pvp.py ...; done 2>&1 | grep " 2H "`, which compiles nothing. Same class:
# decode-build-link.py, find-build-item.py, compare-build-inventory-order.py, parse-build-floor.py.
repo_build_script(word) if {
	parts := split(word, "scripts/")
	some i, tail in parts
	i > 0
	path_component_start(parts[i - 1])
	names := script_name_words(tail)
	count(names) > 0
	some keyword in {"build", "check", "test"}
	keyword in {names[0], names[count(names) - 1]}
}

# `er-build-dlls.sh` -> ["build", "dlls"], `check.sh` -> ["check"], `er-builds-pvp.py` ->
# ["builds", "pvp"]: the basename up to its first dot, cut at `-` and `_`, the `er` prefix dropped.
script_name_words(tail) := names if {
	components := split(tail, "/")
	stem := split(components[count(components) - 1], ".")[0]
	all := [w | some w in split(replace(stem, "_", "-"), "-"); w != ""]
	names := [w | some j, w in all; not er_prefix(all, j)]
}

er_prefix(all, j) if {
	j == 0
	all[0] == "er"
	count(all) > 1
}

path_component_start(prefix) if prefix == ""

path_component_start(prefix) if endswith(prefix, "/")

# The word at `index` is the program its command runs: every word before it is a wrapper, a flag,
# an assignment, or a shell keyword that stands in front of a command. Evaluated only for words
# that already named a build tool or a matcher, so its cost is per candidate, never per word --
# no_rust_edit_without_frida_proof.rego records a per-word version of this exhausting the engine's
# wasm memory on a 3.5 KB command.
in_command_slot(words, index) if {
	count([position |
		some position, _ in words
		position < index
		not slot_prefix(words, position)
	]) == 0
}

slot_prefix(words, position) if commands.command_slot_wrapper_at(words, position)

# Keywords and launchers commands.command_slot_wrapper does not carry, so that `if cargo check ...
# | grep -q error; then`, `! cargo build | grep ...`, `time cargo build | grep ...` and `npx tsc |
# grep ...` still resolve to the build behind them.
slot_prefix(words, position) if words[position] in {"if", "elif", "while", "until", "!", "{", "time", "doas", "npx", "bunx"}

deny contains decision if {
	greps_a_build
	decision := {
		"rule_id": "ER-EFFECTS-NO-GREP-FOR-BUILD-ERRORS",
		"reason": "A build reports success or failure ONCE, at the end, as its EXIT CODE. Grepping its output for 'error' is a guess at which strings a failure prints, and it fails in the direction that matters: on 2026-09-03 `cargo check ... | grep -E 'error' -A6 | head -20` missed an E0603, printed '--- clean ---', and a FAILED build was reported as built while the previous DLL stayed on disk. Run the command and let the exit code answer -- `<cmd>; echo \"exit=$?\"`, or `<cmd> || tail -40 <log>` to read the message only when it actually failed. Piping into head/tail/sed/awk/wc/jq/python to excerpt output is fine and not blocked; so is grepping a log file that is already on disk. For a BACKGROUND build the harness's task exit code describes the wrapper, not the build -- read the build's own log or its provenance record instead.",
		"severity": "HIGH",
	}
}
