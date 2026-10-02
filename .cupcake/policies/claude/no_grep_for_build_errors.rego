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
#     b/c/t/r/d, `cargo xwin ...` -- or rustc, opa, make, ninja, cmake, go, npm/pnpm/yarn, pytest,
#     tsc, or one of this repo's own build scripts) piped into grep/rg/egrep/fgrep/ag/ack.
#     WHAT IS NOT: piping into head/tail/sed/awk/wc/jq/sort/uniq/cut/tr/less/python, which shape or
#     excerpt output rather than adjudicating it; grep ANYWHERE else, including over a build LOG FILE
#     already on disk, which is reading evidence after the exit code has already been believed; a
#     build word that is only an argument or pattern of another program (`pgrep -f 'cargo|rustc' |
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

# Commands whose exit code IS the verdict. `scripts/...` covers this repo's own build wrappers
# (er-build-dlls.sh, check.sh, check-rust-build.sh, ...), which are `set -e` and propagate.
#
# The verb only counts in command position: at the start, after a shell separator (`;` `&` `|` `(`
# backtick, newline, `$(`), or at the start of a `-c '...'` script, optionally behind `VAR=value`
# assignments and a launcher (`timeout 28`, `env`, `bash`, `python3`, `uv run`, ...). An earlier
# version also accepted any whitespace or quote before the verb, so a file NAMED as an argument
# counted as running it: `grep -n x scripts/check-me3-dll-conflicts.py Cargo.toml | head` and a
# `python3 -c` reading `'scripts/check-me3-dll-conflicts.py'` were both denied on 2026-10-02
# while building nothing.
#
# The `scripts/` wrapper alternative needs `build`, `check` or `test` as the first or the last word
# of the script name (`check.sh`, `check-rust-build.sh`, `test-cupcake-policies.py`,
# `act-check.sh`), or the one wrapper that has it in the middle, `er-build-dlls.sh`, by name. Until
# 2026-10-01 it took any name containing those letters, so `scripts/er-builds-*.py` -- PvP
# character-build ranking scripts, nothing to do with compiling -- counted as a build, and a
# delimited word anywhere in the name still caught `er-build-import*`. In this repo `build` in the
# middle of a script name is almost always a character build: `compare-build-armament-slots.py`,
# `decode-build-link.py`, `find-build-item.py`.
#
# Assignments and launchers may interleave (`env RUSTFLAGS=-Dwarnings cargo clippy`), and the shell
# keywords that stand in front of a command (`if`, `!`, `do`, `then`, `{`, ...) and the `npx`/`bunx`
# runners count as launchers, so `if ! cargo check | grep -q error; then` and the body of a
# `for ...; do cargo test ...; done` loop still resolve to the build behind them.
build_command_position := "(^|[;&|(`\\n]|\\$\\(|-c[[:space:]]+['\"])[[:space:]]*(([[:alpha:]_][[:alnum:]_]*=[^[:space:]]*|sudo|doas|env|time|nice|exec|command|xargs|bash|sh|python3?|uv[[:space:]]+run|npx|bunx|timeout[[:space:]]+[0-9.]+[smhd]?|if|elif|while|until|do|then|else|!|\\{)[[:space:]]+)*"

# `cargo` counts only with a verdict subcommand later in the same stage: `cargo tree | grep serde`,
# `cargo metadata | grep` and `cargo --version | grep` read information, not a pass/fail. The
# built-in aliases (`cargo c` is `cargo check`, `b` build, `t` test, `r` run, `d` doc) are the same
# verdict in fewer keystrokes, and `cargo xwin build` is covered because `build` is a later word.
cargo_verdict_pattern := "cargo([[:space:]]+[^;&|[:space:]]+)*[[:space:]]+(build|check|clippy|test|bench|doc|run|nextest|rustc|miri|fix|fmt|install|b|c|t|r|d)"

build_verb_pattern := concat("", [
	build_command_position,
	"(/?([[:alnum:]_.-]+/)*)?(",
	cargo_verdict_pattern,
	"|rustc|opa|make|ninja|cmake|go|npm|pnpm|yarn|pytest|tsc|scripts/([[:alnum:]_.-]+/)*((build|check|test)([-_.][[:alnum:]_.-]*)?|[[:alnum:]_.-]*[-_](build|check|test)(\\.[[:alnum:]]+)?|er-build-dlls\\.sh))($|[^[:alnum:]_-])",
])

# Matchers that ADJUDICATE. head/tail/sed/awk/wc/jq/python are deliberately absent: they excerpt or
# reshape, they do not decide whether the build passed.
#
# The optional `&` after the pipe is bash's merge-stderr-and-pipe shorthand, which puts a character
# between the pipe and the matcher. Without it that spelling walks straight past this guard, which
# is the whole failure mode -- a build whose errors go to stderr is exactly the one that gets piped
# that way.
#
# The optional `;` after it is the newline of a pipe continued onto the next line, as production
# delivers it: `scripts/cupcake-hook.sh` rewrites every unquoted newline to `; ` before the engine
# sees the command, so `cargo build |<newline>grep error` arrives as `cargo build |; grep error`.
# A `;` straight after a pipe is a syntax error in any other spelling, so reading it as the
# continuation cannot misread a real command.
pipe_pattern := `\|&?[[:space:]]*;?[[:space:]]*`

adjudicating_matcher_pattern := concat("", [pipe_pattern, "(/?([[:alnum:]_.-]+/)*)?(grep|egrep|fgrep|rg|ag|ack)($|[[:space:]])"])

# The matcher must consume the build's own output: the build verb and the matcher sit in the same
# pipeline, verb first. Plumbing inside that pipeline still counts -- `cargo build 2>&1 | tee log |
# grep error` and `cargo build |& grep -c error` are the same mistake -- but a grep in a sibling
# command does not. An earlier version matched the verb and the matcher anywhere in the whole
# command line, so on 2026-10-02 `sed -i ... src/imp.rs; grep -rn "the X" src/imp.rs | grep -v
# "X, Y"; cargo fmt -p er-r3-view && python3 scripts/check-comment-caps.py ...` was denied: the
# grep read a source file before anything ran, and the `cargo fmt` after it was piped nowhere.
#
# "Same pipeline" is spelled as what may stand between the verb and the matcher: a run of tokens
# none of which ends a pipeline. The tokens are an ordinary character (anything but `;` `&` `|`, a
# newline or a backslash), a backslash escape -- which takes a line-continuing backslash-newline
# with it -- an inner pipe (`|` or `|&`, then whitespace that may include a newline, then a
# character that is not another separator, so `||` never passes as two pipes), and the redirect
# spellings that carry an `&` without ending anything (`2>&1`, `>&2`, `&>log`). So `;`, `&&`,
# `||`, a lone `&` and an unescaped newline all end the pipeline, while a build continued over two
# lines by a trailing backslash or a trailing `|` stays one. A separator inside a quoted string can
# end it early, which only ever loses a match; it cannot invent one.
#
# The previous version split the command into pipelines with `regex.split` after folding with
# `regex.replace`. Both are host-dispatched in OPA's wasm target and Cupcake's runtime implements
# neither, so in production the split came back undefined and this guard never fired at all, while
# `opa test` -- which runs the interpreter -- passed every case. `scripts/check-cupcake-wasm-builtins.py`
# refuses them for that reason. One `regex.match` per executed text needs neither.
pipeline_body_pattern := concat("", [`([^;&|\n\\]|\\(.|\n)|`, pipe_pattern, `[^;&|[:space:]]|[<>]&|&>)*`])

build_then_matcher_pattern := concat("", [build_verb_pattern, pipeline_body_pattern, adjudicating_matcher_pattern])

# Matched against the shared decomposition rather than the raw command: each text a shell runs, a
# `bash -c '...'` payload as a text of its own, with the separators inside quoted operands and data
# heredoc bodies blanked. So a commit message, a `bd remember` body or a test script written
# through `cat > f <<'EOF'` that quotes `cargo check | grep error` is prose, not a pipeline. That
# one was measured: the hook driver that proves this file through `scripts/cupcake-hook.sh` was
# itself denied while being written, because its quoted test cases are build-into-grep pipelines.
greps_a_build if {
	some text in commands.input_executed_texts
	regex.match(build_then_matcher_pattern, text)
}

# A compound command piped as a whole. `(cargo build; echo done) | grep error`, `{ cargo build; }
# 2>&1 | grep error` and `for c in a b; do cargo test -p $c; done 2>&1 | grep FAILED` put a `;`
# between the build and the pipe, so the same-pipeline pattern above stops at it. When a stage that
# opens with the close of a compound command feeds a matcher, and a build stands in command position
# anywhere in the same executed text, that denies. It is the old co-presence reading, kept only for
# this shape and only in the deny direction.
#
# Built only from `replace`, `split`, `count` and `regex.match`, which Cupcake's wasm runtime runs
# (`scripts/check-cupcake-wasm-builtins.py`).
greps_a_build if {
	some text in commands.input_executed_texts
	regex.match(build_verb_pattern, text)
	some statement in commands.shell_statements(pipeline_text(text))
	stages := split(statement, "|")
	some close_at, stage in stages
	closes_compound(stage)
	some match_at, later in stages
	match_at > close_at
	regex.match(stage_matcher_pattern, later)
}

# `&` spelled inside a redirection is not "run in the background", but shell_statements cuts at
# every `&`: without this, `done 2>&1 | grep error` falls apart into `done 2>` and `1 | grep error`.
# `|&` is bash's `2>&1 |`, so it becomes a plain pipe.
pipeline_text(text) := replace(replace(replace(replace(text, "|&", "|"), ">&", "> "), "<&", "< "), "&>", " >")

stage_matcher_pattern := "^[[:space:]]*(/?([[:alnum:]_.-]+/)*)?(grep|egrep|fgrep|rg|ag|ack)($|[[:space:]])"

# More `)` than `(` (a balanced `$(...)` in the stage is not a close), or a `}`, `done`, `fi` or
# `esac` keyword as the first word.
closes_compound(stage) if count(split(stage, ")")) > count(split(stage, "("))

closes_compound(stage) if {
	words := [word | some word in split(trim_space(stage), " "); word != ""]
	words[0] in {"}", "done", "fi", "esac"}
}

deny contains decision if {
	greps_a_build
	decision := {
		"rule_id": "ER-EFFECTS-NO-GREP-FOR-BUILD-ERRORS",
		"reason": "A build reports success or failure ONCE, at the end, as its EXIT CODE. Grepping its output for 'error' is a guess at which strings a failure prints, and it fails in the direction that matters: on 2026-09-03 `cargo check ... | grep -E 'error' -A6 | head -20` missed an E0603, printed '--- clean ---', and a FAILED build was reported as built while the previous DLL stayed on disk. Run the command and let the exit code answer -- `<cmd>; echo \"exit=$?\"`, or `<cmd> || tail -40 <log>` to read the message only when it actually failed. Piping into head/tail/sed/awk/wc/jq/python to excerpt output is fine and not blocked; so is grepping a log file that is already on disk. For a BACKGROUND build the harness's task exit code describes the wrapper, not the build -- read the build's own log or its provenance record instead.",
		"severity": "HIGH",
	}
}
