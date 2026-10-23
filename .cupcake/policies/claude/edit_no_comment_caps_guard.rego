# METADATA
# scope: package
# title: No Shouted Words In Comments
# description: Capitals in a comment name something; they are never emphasis. Refuse the write.
# custom:
#   severity: MEDIUM
#   id: ER-EFFECTS-COMMENT-CAPS-GUARD
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Write", "Edit", "MultiEdit", "NotebookEdit"]
package cupcake.policies.claude.edit_no_comment_caps_guard

import rego.v1

# WHY A GUARD WHEN `scripts/check-comment-caps.py` ALREADY GATES THIS.
#
# That gate is the authority: it carries 24,627 English words, reads every comment and Python
# docstring in the tree, and knows the exemptions. It also runs at `check.sh` time, which is
# minutes-to-hours after the sentence was written and often a whole session later, and by then the
# shouting is spread across a dozen files. This refuses the write itself, while the author is still
# holding the sentence.
#
# It is DELIBERATELY NARROWER THAN THE GATE, in one direction only: it under-reports. It carries a
# short list of English function words that are never acronyms, never registers and never mnemonics
# in this repo, and it stops reading a line at the first backtick or double quote rather than
# tracking spans. So `the ONLY writer` is refused, `` `NOT` is the x86 instruction `` is not read at
# all, and a shouted word after a quotation on the same line is missed. A miss costs a red gate
# later, which is the status quo; a false positive costs a refused edit the author cannot fix, which
# is worse. The asymmetry is the design.
#
# Two more places where it reads less than the gate, both measured false positives:
#
#   * Fenced code in a Rust comment is not read. The gate drops every comment line between a pair of
#     ``` markers (`FENCE` and `drop_code` in the script), so an asm listing in a `//!` block may
#     annotate `; free the node -- on both paths` in capitals. This toggles on the same regex, line
#     by line, and only in `.rs`: the gate's `#` comment stream never toggles, and this guard reads
#     no Python docstrings. Over all 1017 tracked `.rs` files on 2026-10-23, compared against the
#     script's own `comment_spans` and `drop_code`, a whole-file Write judged by this model read no
#     line the gate drops and skipped no line the gate reads.
#   * An Edit is refused only for a shouted word it adds. Each edit carries the text it replaces,
#     so a word already in `old_string` the same number of times is not new writing: the edit that
#     ported `freelist_shutdown_assert.rs` to 2.7.2.0 rewrote every address of a fenced listing that
#     ends in `on BOTH paths`, and the word was refused although nobody typed it. The same count
#     keeps the baseline's contract text (`do NOT call TitleTopDialog::open_menu ...`) editable
#     around. A Write replaces a file whose previous text this guard cannot see, so all of it counts.
#
# What it still cannot know is whether an Edit starts inside a fence: the opening marker may sit
# above `old_string`. A new shouted word written there is refused, and the reason says to widen the
# edit to take in the opening fence, which this guard then sees.
#
# The word list is the head of the measured distribution, from the sweep that took this tree to
# zero: `NOT` 1637, `THE` 1603, `IS` 750, `THIS` 518, `ONLY` 483, `ONE` 465, `BEFORE` 302, `SAME`
# 286, `EVERY` 241, `FIRST` 240. Excluded on purpose: `AND`, `OR`, `SET`, `CALL`, `TEST`, `PUSH`,
# `OUT`, `IN` -- all x86 mnemonics this repo quotes constantly.
shouted_words_re := `\b(NOT|THE|THIS|THAT|ONLY|ONE|TWO|EVERY|NEVER|ALWAYS|MUST|SAME|BOTH|OWN|WHY|HOW|WHAT|WHICH|WHO|BEFORE|AFTER|FIRST|SECOND|NOTHING|ANY|ALL|HERE|ACTUALLY|EXACTLY|REAL|WRONG|INSTEAD|WITHOUT|WHOLE|ONCE|STILL|EITHER|NEITHER|RATHER|ITSELF|EACH|THEN|THAN|BECAUSE|CANNOT|ALREADY|DELIBERATELY|GENUINELY|SILENTLY|MEASURED|PROVEN|IDENTICAL|IS|WAS|ARE|SHOULD|WOULD|COULD)\b`

# The file types `scripts/check-comment-caps.py` scans. Vendored upstream source is excluded there
# and here: its prose belongs to its authors.
scanned_exts := {".rs", ".py", ".sh", ".bash"}

tool_input := object.get(input, "tool_input", {})

file_path := object.get(tool_input, "file_path", object.get(tool_input, "path", ""))

lower_tool_name := lower(object.get(input, "tool_name", ""))

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	scanned_file
	some pair in written_pairs
	added := added_words(pair)
	count(added) > 0
	some line in shouted_lines(pair.new)
	some m in regex.find_all_string_submatch_n(shouted_words_re, quotable_prefix(line), -1)
	m[0] in added

	decision := {
		"rule_id": "ER-EFFECTS-COMMENT-CAPS-GUARD",
		"severity": "MEDIUM",
		"reason": concat("", [
			"🧁 Cupcake stopped a shouted word going into a comment in ",
			file_path,
			":\n\n    ",
			trim_space(line),
			"\n\nCapitals in a comment name something -- a const/static, an env var, an acronym, an x86 register or mnemonic, a file or section name, an upstream FUN_<addr> symbol. They are never emphasis. This tree was swept to zero shouted words on 2026-09-07 and `scripts/check-comment-caps.py` (wired into check.sh) holds it there, so this edit would go red at push time.",
			"\n\nHappy path: lowercase the word. If it really is a name -- the x86 NOT, a \"NOT RUN\" status string, a constant that happens to be an English word -- put it in backticks and this guard stops reading there. If it is load-bearing, the fix is usually a rewritten first sentence, not a louder one. See AGENTS.md, `Conventions & Patterns`.",
			"\n\nIf the line sits inside a ``` fenced block in a Rust comment, it is code and the gate does not read it; this guard cannot see a fence that opens above the edit, so widen the edit to take in the opening ``` line.",
		]),
	}
}

# Only the tools that author file content, and only the field each one writes, paired with the text
# it replaces. A Write or NotebookEdit replaces text this guard cannot see, so its `old` is empty.
written_pairs contains {"old": "", "new": text} if {
	lower_tool_name in {"write", "notebookedit"}
	text := object.get(tool_input, "content", object.get(tool_input, "new_source", ""))
}

written_pairs contains {"old": object.get(tool_input, "old_string", ""), "new": object.get(tool_input, "new_string", "")} if {
	lower_tool_name == "edit"
}

written_pairs contains {"old": object.get(edit, "old_string", ""), "new": object.get(edit, "new_string", "")} if {
	lower_tool_name == "multiedit"
	some edit in object.get(tool_input, "edits", [])
}

# The shouted words a pair writes more often than the text it replaces held them.
added_words(pair) := {w |
	new := shouted_words(pair.new)
	old := shouted_words(pair.old)
	some w in new
	count([x | some x in new; x == w]) > count([x | some x in old; x == w])
}

# Every shouted word on `shouted_lines(text)`, once per occurrence. The pattern carries one group
# between a zero-width `\b` pair, so a match's whole text is the word.
shouted_words(text) := [m[0] |
	some line in shouted_lines(text)
	some m in regex.find_all_string_submatch_n(shouted_words_re, quotable_prefix(line), -1)
]

# The comment lines of `text` outside a fenced block that carry a shouted word.
# Counting markers before a line stands in for the gate's toggle: an odd count is inside a block.
shouted_lines(text) := [line |
	lines := split(text, "\n")
	markers := fence_markers(lines)
	some i, line in lines
	not i in markers
	count([m | some m in markers; m < i]) % 2 == 0
	shouted_line(line)
]

# The gate's `FENCE` regex, `^\s*(?://+!?|\*)?\s*` then three backticks, which a raw string cannot
# hold. Rust only: the gate's `#` comment stream never toggles, so a `.py` or `.sh` line is read
# whatever surrounds it.
fence_re := "^\\s*(?://+!?|\\*)?\\s*```"

fence_markers(lines) := {i |
	endswith(file_path, ".rs")
	some i, line in lines
	regex.match(fence_re, line)
}

scanned_file if {
	some ext in scanned_exts
	endswith(file_path, ext)
	not vendored
}

# Both spellings reach this guard: an absolute path from the harness, and the repo-relative one a
# test (or a relative edit) carries.
vendored if contains(file_path, "/third_party/")

vendored if startswith(file_path, "third_party/")

shouted_line(line) if {
	comment_line(line)

	# A comment-shaped line inside a string literal is generated source, not prose: `build.rs`
	# writes Rust that contains comments, and those carry an escaped newline on every line.
	not contains(line, "\\n")

	regex.match(shouted_words_re, quotable_prefix(line))
}

comment_line(line) if startswith(trim_space(line), "//")

comment_line(line) if startswith(trim_space(line), "#")

# Everything before the first backtick or double quote. Past that point a shouted word is probably
# being QUOTED rather than shouted, and this guard would rather miss one than refuse a correct edit.
quotable_prefix(line) := substring(line, 0, cut) if {
	cut := first_quote(line)
}

first_quote(line) := tick if {
	tick := quote_at(line, "`")
	quote := quote_at(line, "\"")
	tick <= quote
}

first_quote(line) := quote if {
	tick := quote_at(line, "`")
	quote := quote_at(line, "\"")
	quote < tick
}

quote_at(line, mark) := at if {
	at := indexof(line, mark)
	at >= 0
} else := count(line)
