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
#     WHAT IS BLOCKED: a build-ish command (cargo, rustc, opa, make, ninja, cmake, go, npm/pnpm/yarn,
#     pytest, tsc, or one of this repo's own build scripts) piped into grep/rg/egrep/fgrep/ag/ack.
#     WHAT IS NOT: piping into head/tail/sed/awk/wc/jq/sort/uniq/cut/tr/less/python, which shape or
#     excerpt output rather than adjudicating it; grep ANYWHERE else, including over a build LOG FILE
#     already on disk, which is reading evidence after the exit code has already been believed; and
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

command := object.get(input.tool_input, "command", "")

# Commands whose exit code IS the verdict. Matched as a token at a statement start or after a shell
# separator so `mycargo` / `not-make` never match. `scripts/...` covers this repo's own build
# wrappers (er-build-dlls.sh, check.sh, check-rust-build.sh, ...), which are `set -e` and propagate.
build_verb_pattern := "(^|[[:space:];|&('\"`])(/?([[:alnum:]_.-]+/)*)?(cargo|rustc|opa|make|ninja|cmake|go|npm|pnpm|yarn|pytest|tsc|scripts/[[:alnum:]_./-]*(build|check|test)[[:alnum:]_./-]*)($|[^[:alnum:]_-])"

# Matchers that ADJUDICATE. head/tail/sed/awk/wc/jq/python are deliberately absent: they excerpt or
# reshape, they do not decide whether the build passed.
#
# The optional `&` after the pipe is bash's merge-stderr-and-pipe shorthand, which puts a character
# between the pipe and the matcher. Without it that spelling walks straight past this guard, which
# is the whole failure mode -- a build whose errors go to stderr is exactly the one that gets piped
# that way.
adjudicating_matcher_pattern := "\\|&?[[:space:]]*(/?([[:alnum:]_.-]+/)*)?(grep|egrep|fgrep|rg|ag|ack)($|[[:space:]])"

# The pipeline must both start from a build verb and route it into a matcher. Checking the whole
# command (rather than per-segment) is deliberate: `cargo build 2>&1 | tee log | grep error` and
# `cargo build |& grep -c error` are the same mistake with more plumbing.
greps_a_build if {
	regex.match(build_verb_pattern, command)
	regex.match(adjudicating_matcher_pattern, command)
}

deny contains decision if {
	greps_a_build
	decision := {
		"rule_id": "ER-EFFECTS-NO-GREP-FOR-BUILD-ERRORS",
		"reason": "A build reports success or failure ONCE, at the end, as its EXIT CODE. Grepping its output for 'error' is a guess at which strings a failure prints, and it fails in the direction that matters: on 2026-09-03 `cargo check ... | grep -E 'error' -A6 | head -20` missed an E0603, printed '--- clean ---', and a FAILED build was reported as built while the previous DLL stayed on disk. Run the command and let the exit code answer -- `<cmd>; echo \"exit=$?\"`, or `<cmd> || tail -40 <log>` to read the message only when it actually failed. Piping into head/tail/sed/awk/wc/jq/python to excerpt output is fine and not blocked; so is grepping a log file that is already on disk. For a BACKGROUND build the harness's task exit code describes the wrapper, not the build -- read the build's own log or its provenance record instead.",
		"severity": "HIGH",
	}
}
