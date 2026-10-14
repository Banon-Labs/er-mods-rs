# METADATA
# scope: package
# title: Refuse a pull request title CI will reject
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-PR-TITLE-CONVENTIONAL
#   description: >-
#     Refuse `gh pr create` / `gh pr edit` whose --title is not a Conventional
#     Commits subject. The `pr-title` job runs
#     scripts/conventional-commit-subject.py against the PR title and fails the
#     check suite; nothing local said so until the red X arrived.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.gh_pr_title_conventional

import rego.v1

# A commit subject is gated locally by a hook; the PR title was not gated anywhere.
#
# 2026-09-09: PR #421 opened with the title "Cancel a local invasion that lands in the wrong
# place" and the `pr-title` job failed five seconds later --
# `scripts/conventional-commit-subject.py --subject "$PR_TITLE"` refusing it for having no type.
# The identical rule already fires on every commit through
# `scripts/hooks/commit-msg`, so the type was written correctly fifteen times in a row on the
# same branch and then omitted at the one place nothing was checking.
#
# The pattern here is a deliberate subset of the python checker: the type list and the
# `<type>[(scope)][!]: <description>` shape, which is what the failure was. Length limits,
# trailing punctuation and mood are left to the gate that owns them -- this is a cheap pre-flight
# at the moment the title is typed, not a second implementation to drift against the first.
# `scripts/conventional-commit-subject.py` remains the authority.

command := object.get(input.tool_input, "command", "")

# A cheap pre-filter over the raw text, so the tokenizer below only runs on commands that
# mention `gh pr create` or `gh pr edit`, the two commands that set a title. It is deliberately
# loose -- a quote, a slash or a parenthesis may stand before `gh` -- because the decision is made
# on the tokens, not here.
gh_pr_title_command if {
	regex.match(`(^|[[:space:];|&('"\x60/])gh[[:space:]]+pr[[:space:]]+(create|edit)([[:space:];|&)'"\x60]|$)`, command)
}

# Whose `--title` is it.
#
# 2026-10-13: this refused
#
#     python3 scripts/kitty-user-command.py --title 'push python-write guard fix' --why '...' \
#         -- 'git ... push ... && gh pr create ... --title "fix(cupcake): admit ...; close ..." ...'
#
# naming `push python-write guard fix` -- the kitty tab's title, an argument to a different
# program. The old rule took the first `--title` of each quoting style anywhere in the command,
# so the single-quoted tab title won and the real, valid PR title was never read.
#
# A title now has to belong to the `gh pr create` / `gh pr edit` invocation itself: the words
# after `gh pr <verb>` up to the end of that simple command. The command is split into shell
# words -- quote-aware, so the `;` inside the PR title above does not end the command -- and each
# quoted word is unquoted and split again, because a `gh pr create` handed to another program
# as one quoted argument (`bash -c '...'`, the kitty helper's `-- '...'`) still runs and is still
# judged. Three levels of that nesting are read; `bash -c "ssh h 'gh pr create ...'"` is two.

# One shell word -- double-quoted, single-quoted, backslash-escaped and bare runs glued together,
# as in `--title="a b"` -- or one control operator. Whitespace between words matches nothing and
# is skipped. A backslash-newline is an escaped character inside a word, which is what keeps a
# line continuation from reading as a command separator.
token_pattern := `(?:"(?:[^"\\]|\\[\s\S])*"|'[^']*'|\\[\s\S]|[^\s;|&()<>"'\\\x60])+|&&|\|\||[;|&()\x60\n]`

# The pieces of one word, for unquoting it.
piece_pattern := `"(?:[^"\\]|\\[\s\S])*"|'[^']*'|\\[\s\S]|[^"'\\]+`

operator_tokens := {"&&", "||", ";", "|", "&", "(", ")", "`", "\n"}

# `regex.find_all_string_submatch_n` rather than `regex.find_n`: Cupcake's WASM runtime does not
# implement `regex.find_n`, so it returns undefined there and the guard falls silent while
# `opa test` passes (`scripts/check-cupcake-wasm-builtins.py` names it). Neither pattern has a
# capture group, so element 0 of each match is the whole match.
tokens(text) := [m[0] | some m in regex.find_all_string_submatch_n(token_pattern, text, -1)]

# What the shell hands the program for this word.
word_value(word) := concat("", [piece_literal(m[0]) | some m in regex.find_all_string_submatch_n(piece_pattern, word, -1)])

piece_literal(p) := out if {
	startswith(p, `"`)
	out := double_quoted_literal(substring(p, 1, count(p) - 2))
} else := out if {
	startswith(p, "'")
	out := substring(p, 1, count(p) - 2)
} else := out if {
	startswith(p, `\`)
	out := substring(p, 1, -1)
} else := p

# Inside double quotes a backslash escapes only `"`, `\`, `$` and the backtick. Escaped
# backslash pairs are parked first so `\\"` is not read as an escaped quote. `regex.replace` is
# not used because the WASM runtime does not implement it.
double_quoted_literal(s) := out if {
	parked := replace(s, `\\`, backslash_marker)
	unescaped := replace(replace(replace(parked, `\"`, `"`), `\$`, "$"), "\\`", "`")
	out := replace(unescaped, backslash_marker, `\`)
}

backslash_marker := "__gh_pr_title_escaped_backslash__"

# Memory is the constraint here, not speed. Cupcake runs every policy in one WASM module with a
# bounded heap (10 MB by default, 100 MB under `scripts/cupcake-hook.sh`), and an allocation that
# does not fit aborts the whole evaluation, which the hook reports as a refusal of the command.
# So each text is tokenized once, and a word is unquoted only when it can matter: a candidate for
# nesting, or a title value. The command words themselves (`gh`, `pr`, `create`, `--title`) are
# compared as typed, so a quoted `"gh"` or `"--title"` is not recognised -- the old rule did not
# see those either.

# Token lists for the quoted words of these lists that could hold a `gh pr` command. Plain
# substring tests pick the candidates before anything is unquoted. A word that is itself an
# argument of `gh pr create` / `gh pr edit` is data gh never runs -- a `--body` that quotes an
# example command is the usual one -- so it is not read as a command.
nested_token_lists(lists) := {tokens(word_value(word)) |
	some toks in lists
	some k, word in toks
	nesting_candidate(word)
	not inside_gh_arguments(toks, k)
}

# The `[start, end)` index ranges of the arguments of every `gh pr create` / `gh pr edit` in
# `toks`: the words after `gh pr <verb>` up to the end of that simple command.
gh_segments(toks) := {[i + 3, segment_end(toks, i + 3)] |
	some i
	gh_word(toks[i])
	toks[i + 1] == "pr"
	toks[i + 2] in {"create", "edit"}
}

inside_gh_arguments(toks, k) if {
	some seg in gh_segments(toks)
	k >= seg[0]
	k < seg[1]
}

nesting_candidate(word) if {
	contains(word, "gh")
	contains(word, "pr")
	contains(word, "create")
	quoted_or_escaped(word)
}

nesting_candidate(word) if {
	contains(word, "gh")
	contains(word, "pr")
	contains(word, "edit")
	quoted_or_escaped(word)
}

quoted_or_escaped(word) if contains(word, `"`)

quoted_or_escaped(word) if contains(word, "'")

quoted_or_escaped(word) if contains(word, `\`)

token_lists0 := {tokens(command)}

token_lists1 := nested_token_lists(token_lists0)

token_lists2 := nested_token_lists(token_lists1)

token_lists3 := nested_token_lists(token_lists2)

token_lists := ((token_lists0 | token_lists1) | token_lists2) | token_lists3

gh_word(w) if w == "gh"

gh_word(w) if endswith(w, "/gh")

# Index just past the last word of the simple command that starts at `start`.
segment_end(toks, start) := min({k | some k; toks[k]; k >= start; toks[k] in operator_tokens} | {count(toks)})

# The title a word at `j` sets, if it is a title flag with its value inside the segment.
title_at(toks, j, end) := word_value(toks[j + 1]) if {
	toks[j] in {"--title", "-t"}
	j + 1 < end
}

title_at(toks, j, _) := substring(word_value(toks[j]), 8, -1) if startswith(toks[j], "--title=")

title_at(toks, j, _) := substring(word_value(toks[j]), 3, -1) if startswith(toks[j], "-t=")

title_flag(w) if w in {"--title", "-t"}

title_flag(w) if startswith(w, "--title=")

title_flag(w) if startswith(w, "-t=")

# One title per `gh pr create` / `gh pr edit` invocation: the last title flag in it, which is
# the one gh keeps when the flag is repeated.
titles contains title if {
	some toks in token_lists
	some seg in gh_segments(toks)
	flags := {j | some j; toks[j]; j >= seg[0]; j < seg[1]; title_flag(toks[j])}
	count(flags) > 0
	title := title_at(toks, max(flags), seg[1])
}

# The Conventional Commits shape the CI job requires, and the same type list its error message
# prints. `!` marks a breaking change and is allowed between the scope and the colon.
conventional(subject) if {
	regex.match(`^(feat|fix|chore|docs|style|refactor|perf|test|build|ci|revert)(\([^)\n]+\))?!?: .+`, subject)
}

offending_titles contains t if {
	gh_pr_title_command
	some t in titles
	not conventional(t)
}

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	some subject in offending_titles

	decision := {
		"rule_id": "ER-EFFECTS-PR-TITLE-CONVENTIONAL",
		"severity": "HIGH",
		"reason": concat("", [
			"This pull request title is not a Conventional Commits subject, and CI's `pr-title` job ",
			"runs scripts/conventional-commit-subject.py against it -- so the check suite goes red ",
			"about five seconds after the PR is opened. Expected `<type>[(scope)][!]: ",
			"<description>` where the type is one of feat, fix, chore, docs, style, refactor, perf, ",
			"test, build, ci, revert. The type is what Release Please reads to pick the next ",
			"version: feat -> minor, fix -> patch, ! -> major.\n\n  title : ",
			subject,
			"\n\nRun `python3 scripts/conventional-commit-subject.py --subject \"<title>\"` to see ",
			"the authoritative verdict.",
		]),
	}
}
