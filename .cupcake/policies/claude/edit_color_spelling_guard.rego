# METADATA
# scope: package
# title: Write color, not the British spelling
# description: Refuse an Edit that adds the British spelling of color. The ratchet is scripts/check-color-spelling.py.
# custom:
#   severity: MEDIUM
#   id: ER-EFFECTS-COLOR-SPELLING-GUARD
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Edit", "MultiEdit"]
package cupcake.policies.claude.edit_color_spelling_guard

import rego.v1

# Why a guard when `scripts/check-color-spelling.py` already gates this.
#
# That gate is the authority: it counts every tracked text file against
# `scripts/color-spelling.baseline.json`, in both directions. It runs at check.sh time, which can be
# a session after the word was typed. This refuses the edit itself, while the author still has it.
#
# It is narrower than the gate in one direction only, and on purpose: it under-reports.
#
#   * It judges Edit and MultiEdit, never Write. An edit carries the text it replaces, so "this
#     edit adds one" is a fact the guard can read. A Write replaces a whole file whose previous
#     count this guard cannot see, so judging it would refuse the rewrite of every file the
#     baseline already counts.
#   * Text inside a double-quoted span is not read. A planner share-link key or the
#     er-cel-shade.toml levels key is quoted wherever it is written, and those are the contracts the
#     baseline's `_reasons` record. Prose and identifiers are not quoted, and they are what the
#     ratchet exists to stop.
#
# A miss costs a red gate later. A false positive costs an edit the author cannot make.
#
# The pattern is spelled `colo[u]r` so this file carries none of what it counts.
banned_re := `(?i)colo[u]r`

quoted_span_re := `"[^"\n]*"`

tool_input := object.get(input, "tool_input", {})

file_path := object.get(tool_input, "file_path", object.get(tool_input, "path", ""))

lower_tool_name := lower(object.get(input, "tool_name", ""))

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	not skipped_file
	some pair in edit_pairs
	added := occurrences(pair.new) - occurrences(pair.old)
	added > 0

	decision := {
		"rule_id": "ER-EFFECTS-COLOR-SPELLING-GUARD",
		"severity": "MEDIUM",
		"reason": concat("", [
			"🧁 Cupcake stopped an edit to ",
			file_path,
			" that adds ",
			format_int(added, 10),
			" British spelling(s) of `color`.",
			"\n\nThis repo writes `color`, in prose, identifiers and messages alike. `scripts/check-color-spelling.py` (check.sh, lint stage) ratchets every tracked file against `scripts/color-spelling.baseline.json`, so this edit would go red at push time.",
			"\n\nHappy path: write `color`. If the word is another program's key -- a build-planner field, a config key users already have in their toml -- it is quoted, and quoted text is not read here; raise the file's baseline number and add a `_reasons` line naming where that contract lives. See AGENTS.md, `Conventions & Patterns`.",
		]),
	}
}

# Each edit as the text it removes and the text it puts in its place.
edit_pairs contains {"old": object.get(tool_input, "old_string", ""), "new": object.get(tool_input, "new_string", "")} if {
	lower_tool_name == "edit"
}

edit_pairs contains {"old": object.get(edit, "old_string", ""), "new": object.get(edit, "new_string", "")} if {
	lower_tool_name == "multiedit"
	some edit in object.get(tool_input, "edits", [])
}

occurrences(text) := count(regex.find_n(banned_re, regex.replace(text, quoted_span_re, ""), -1))

# The gate skips `Cargo.lock`, which cargo writes and nobody edits by hand.
skipped_file if endswith(file_path, "Cargo.lock")
