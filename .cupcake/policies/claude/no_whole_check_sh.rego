# METADATA
# scope: package
# title: The agent does not run check.sh
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-CHECK-SH
#   description: >-
#     Hard block on an agent Bash command that runs `scripts/check.sh`, in any
#     form, with or without `--stage`. The pre-push hook runs it and CI runs it,
#     both on a box with no game on it, and neither needs the agent's help.
#
#     The user asked for this as a question on 2026-09-18 -- "why would I ever
#     want to run check.sh manually? And why would I want to allow you to run it
#     ever?" -- while a suite this agent had started in the background pinned
#     every core under a game this agent had also launched for them. The agent
#     answered with a `bd` memory and a refusal inside `check.sh`, and the user
#     rejected both: "If it's a rule, then you're bound by it. Since you weren't
#     bound by it, and recorded anyway, it's not a rule. Rego policies are rules."
#     A first draft of this file still exempted `--stage`; the user removed that
#     too -- "How about never run check.sh period?"
#
#     That is the whole reasoning. A memory is advisory and the next agent reads
#     it or does not. A refusal inside `check.sh` fires only once the command is
#     already running, and it is the agent's own code, which is the thing under
#     question. Only a `PreToolUse` deny stops the agent before the act.
#
#     What the agent runs instead is the specific gate its edit touched, named
#     directly and by its own path -- `cargo test -p <crate>`,
#     `cargo fmt -p <crate> -- --check`, `python3 scripts/check-<gate>.py`. Naming
#     them is the agent's work; it is the same reasoning already done to decide
#     what to edit, and `require_scoped_cargo` records at length what delegating
#     it to the build system costs.
#
#     Deliberately no override. `git_block_any_push` is the model: an escape hatch
#     an agent can type is an escape hatch an agent will type. The person who
#     wants the suite runs it themselves -- policies gate the agent's Bash tool,
#     never the user's terminal.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.no_whole_check_sh

import rego.v1

import data.cupcake.system.commands

# The executed decomposition, not the raw command string, for the reason recorded
# at length in `.cupcake/system/commands.rego`: anchoring on lexical position is
# wrong in both directions at once. It misses `bash -c 'bash scripts/check.sh'`,
# a wrapper form AGENTS.md actively recommends for fish, and it fires on a `bd`
# memory body, a commit message or a doc that merely quotes the command.
#
# That second half is what makes this file writable at all. `require_scoped_cargo`
# records a first draft of itself that "did deny the gate by name and immediately
# blocked the edit that was removing it, because the file being edited contains
# the string", and concludes that a guard naming this script cannot be written.
# It can, on this decomposition, and the tests pin both directions -- including a
# commit message and a `bd remember` body naming the command.
executed_texts := commands.input_executed_texts

# `check.sh` reached from a command slot, however it is spelled: `bash
# scripts/check.sh`, `sh ./scripts/check.sh`, an absolute path, the script invoked
# directly, or any of those behind `timeout`/`env`/an assignment.
#
# Token operations rather than one pattern. The engine evaluates policies as wasm
# modules, and a regex in a rule crashed every policy at once on 2026-09-14 (bd
# `a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14`);
# `teardown_must_relaunch` was rewritten off regexes for the same reason.
check_script_word(word) if endswith(word, "check.sh")

# The word split and the command-slot test were written here first and now live in
# `.cupcake/system/commands.rego`, because the next two policies that needed them were
# about to transcribe them (`teardown_must_relaunch`, `bash_no_python_file_write`) and a
# second copy of a decomposition is the divergence bug that package's own header warns
# about. The behaviour is unchanged; the reasoning that produced it is recorded there.
runs_check_sh if {
	some text in executed_texts
	words := commands.command_slot_words(text)
	spans := data_heredoc_spans(words)
	some index, word in words
	check_script_word(word)

	# Before the command-slot test rather than after it: the slot test walks back
	# through the word list, and a body that names the script on every line would pay
	# for that walk once per mention.
	not index_inside(spans, index)
	commands.word_in_command_slot(words, index)
}

# ---------------------------------------------------------------------------
# A heredoc body no shell reads is prose (2026-09-22)
#
# `commands.heredoc_body_blanked` already means to neutralise a heredoc body that a
# non-shell command reads, and in the opa interpreter it does. In production it never
# fires: it locates the body by looking for `"\n"` plus the tag, and the engine has
# replaced every unquoted newline with a space before a policy runs. That is recorded
# as dead logic and measured by `scripts/test-cupcake-delivered-shape.py`, so the body
# arrives welded onto the command that reads it, with its own `;`, `&`, `|` and `(`
# still standing as command positions.
#
# So a pull-request body written with `cat > <file> <<'EOF' ... EOF` hands this rule a
# command slot per parenthesis. Measured 2026-09-22 on the real denial: the whole body
# below became one line, and the word after the `(` was read as a program.
#
#     test-check-sh-accumulates.py   PASS   (check.sh changed)
#
# Nothing in that command runs the suite; the file write was refused and the pull
# request went unedited. The narrowing below is per policy rather than in the shared
# decomposition on purpose -- neutralising every data heredoc for every policy would
# change what the destructive and protected-path guards see, which is not this fix's
# to decide.
#
# The condition is not "written to a file", which was the first draft and was wrong in
# the same direction as the bug: `git commit -F - <<'EOF'` carries no redirect, and this
# file's own commit message was refused by it. What matters is whether a shell is fed
# the body. Two tests, and both are about the opener alone:
#
#   * nothing in the command that opens the heredoc may be a shell, so `bash <<'EOF'`
#     keeps its body as a program;
#   * no shell may be reachable across the rest of the opener's line -- the tokens that
#     can still stand there once the newline is gone are separators, redirects, file
#     descriptors and a line continuation. That is what keeps `cat <<'EOF' | bash`,
#     `cat <<'EOF' \ | bash`, `tee f <<'EOF' | bash` and
#     `cat > f <<'EOF' ; bash scripts/check.sh ; EOF` denied, while a body that merely
#     opens with a markdown table is prose: its first word is not a token a shell line
#     can hold, so the scan stops there.
#
# An invocation after the terminator is outside the body and is denied as before.
#
# Residue, stated rather than hidden, and pinned by tests rather than described. The
# first word carrying the tag closes the span, so a body line that repeats it ends the
# exemption early; and a body whose first line is itself a shell invocation behind a
# separator (`; bash <path>`) cannot be told from a statement on the opener's own line.
# Both of those deny, which is the direction this guard errs in.
#
# Computed once per text, before the match iterates, and that placement is the whole
# performance story. A body the exemption covers produces no match at all, so the
# engine cannot stop early -- it tries every word -- and a span test written per
# candidate scans the word list again each time. Measured on a 32 KB body naming the
# script 400 times: per candidate, 17.7s and the wasm module out of memory; as a set
# computed once, well under a second. The out-of-memory path is a refusal, so the cost
# was not merely slow, it denied.
data_heredoc_spans(words) := {[open, close] |
	some open, word in words
	tag := heredoc_open_tag(word)
	data_heredoc_opener(words, open)
	closes := [i |
		some i, w in words
		i > open
		tag in word_lines(w)
	]
	count(closes) > 0
	close := min(closes)
}

index_inside(spans, index) if {
	some span in spans
	span[0] < index
	index < span[1]
}

# `commands.command_slot_words` splits on spaces, so a newline that survived
# enrichment glues its neighbours into one word. That happens whenever `scan_text`
# falls back to the raw command -- a backtick in a markdown body is enough -- and the
# terminator of the measured case arrived as the single word `call.\nEOF\ntimeout`.
# Reading a word as its lines is what finds it. Only the heredoc span is read this way:
# the match itself keeps the shared tokenisation, so nothing that is denied today stops
# being denied because a newline was split.
word_lines(word) := split(replace(word, "\r", "\n"), "\n")

# The tag of a word that opens a heredoc: `<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`.
# Token operations rather than a regex, for the reason the header of this file records.
# A word this cannot read whole (`<<'EOF'$`, a bare `<<` from `x << 2`) yields a tag no
# terminator word can carry, so the body is never located and the deny stands.
heredoc_open_tag(word) := tag if {
	startswith(word, "<<")
	not startswith(word, "<<<")
	parts := split(word, "<<")
	count(parts) == 2
	unquoted := replace(replace(word_lines(parts[1])[0], "'", ""), `"`, "")
	tag := trim_prefix(unquoted, "-")
	tag != ""
}

# The words of the command that opens the heredoc, from its command slot to the opener.
heredoc_opener_command(words, open) := [w |
	some i, w in words
	i >= commands.command_slot_start(words, open)
	i < open
]

data_heredoc_opener(words, open) if {
	count([w |
		some w in heredoc_opener_command(words, open)
		shell_name(w)
	]) == 0
	not shell_on_the_opener_line(words, open)
}

# A shell reached from the opener across nothing but tokens a command line can still
# hold. The scan stops at the first word that is none of those, which in a data heredoc
# is the first word of the body.
shell_on_the_opener_line(words, open) if {
	some i, w in words
	i > open
	shell_name(w)
	count([j |
		some j, x in words
		j > open
		j < i
		not opener_line_word(x)
	]) == 0
}

# `|`, `;`, `&` as the decomposition spells them; `>file`, `2>`, `<&-` and their kin;
# a file descriptor number; and the backslash that joins the opener's line to the next.
opener_line_word(word) if word == commands.command_slot_separator

opener_line_word(word) if word == "\\"

opener_line_word(word) if contains(word, ">")

opener_line_word(word) if startswith(word, "<")

opener_line_word(word) if {
	word != ""
	count([c |
		some c in split(word, "")
		not digit_char(c)
	]) == 0
}

digit_char(c) if c in {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}

# The shells `commands.shell_name_pattern` recognises, as names rather than as a regex:
# sh, bash, zsh, ksh, dash, ash, fish, csh, tcsh, and the three words that read a body
# as a program without being one of them.
shell_name(word) if word in {"sh", "bash", "zsh", "ksh", "dash", "ash", "fish", "csh", "tcsh", "eval", "source", "."}

shell_name(word) if {
	some name in {"/sh", "/bash", "/zsh", "/ksh", "/dash", "/ash", "/fish", "/csh", "/tcsh"}
	endswith(word, name)
}

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	runs_check_sh

	decision := {
		"rule_id": "ER-EFFECTS-NO-CHECK-SH",
		"reason": "The agent does not run scripts/check.sh. The pre-push hook runs it and CI runs it, on a box with no game on it. Run the specific gate your edit touched instead, by its own path -- and in the MODE check.sh runs it, which is where this list used to mislead: `cargo test -p <crate>` AND `cargo clippy -p <crate> --all-targets` (the workspace denies warnings, and `cargo test` never invokes clippy), `cargo fmt -p <crate> -- --check`, and `python3 scripts/check-<gate>.py --selftest` BEFORE `python3 scripts/check-<gate>.py` (most gates have both, check.sh runs both, and a gate that passes on your tree can still have a broken selftest). Touching a scripts/*.py gate also means `python3 scripts/check-stages.py --selftest`, which proves every path a gate reads is declared by its stage. Naming them is your work, and it is seconds.",
		"severity": "HIGH",
	}
}
