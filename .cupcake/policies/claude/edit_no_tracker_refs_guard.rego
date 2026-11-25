# METADATA
# scope: package
# title: Comments carry no beads ids, PR numbers or branch names
# description: Refuse a Write or Edit that adds a beads id, pull request number or branch name to a comment. The gate is scripts/check-no-tracker-refs.py.
# custom:
#   severity: MEDIUM
#   id: ER-EFFECTS-NO-TRACKER-REFS-GUARD
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Write", "Edit", "MultiEdit"]
package cupcake.policies.claude.edit_no_tracker_refs_guard

import rego.v1

# User request 2026-11-24: "I would like to remove mention of beads issues and PRs and branches
# from comments throughout the codebase". The history lives in git and bd; a comment says what the
# code does and why.
#
# Why a guard when `scripts/check-no-tracker-refs.py` already gates this: that gate runs at
# check.sh time, often a session after the comment was written. This refuses the write itself,
# while the author still has the sentence.
#
# It reads less than the gate, on purpose, because a false positive costs an edit nobody can make
# and a miss costs a red gate later:
#
#   * Only whole-line comments: a line that starts with `//` in the C-like languages, `#` in the
#     hash ones, `--` in Lua. A trailing comment after code, a `/* */` body line and a Python
#     docstring are the gate's to read, because telling them from a string literal needs the
#     tokeniser this guard does not have.
#   * An Edit is refused only for mentions it adds: each edit carries the text it replaces, so a
#     mention already in `old_string` as often is not new writing. A Write is judged whole, since
#     the gate holds every file at zero and a rewrite that keeps one keeps a red gate.
#
# The patterns and their exclusions are the gate's: see its docstring for the false positives
# measured over the tree (directories that share a branch prefix, one-word slugs, ordinals such as
# the first hit of a combo, counters, hex colors). Go's regex engine has no lookaround, so each
# boundary the gate writes as a lookbehind is consumed as a character here, or captured and judged
# by the rule. The beads prefixes are spelled with a character class so this file holds neither.
bd_re := `\b(?:er-effect[s]-rs|er-mod[s]-rs)-[a-z0-9]{3,5}(?:\.\d+)*\b(-?)`

bsg_re := `\bbsg-[a-z0-9]{3}\b(-?)`

pr_word_re := `\bPRs?[\s-]*#?\d+|(?i:\bpull[\s-]+requests?)[\s-]*#?\d+`

pr_suffix_re := `\(#\d{2,}\)`

pr_url_re := `/pull/\d+`

# A bare three-digit number, and the word before it: one that counts (run, hit, frame, R1 ...) or
# names a pull request already counted above makes it not a bare mention.
pr_bare_re := `(?i)(\b(?:PRs?|requests?|runs?|calls?|hits?|frames?|writes?|points?|lines?|kicks?|slots?|steps?|rows?|entry|entries|items?|attempts?|iterations?|samples?|R1|R2|L1|L2)\s*)?(?:^|[^\w#,~&/.-])#\d{3}(?:[^\w#]|$)`

branch_re := `(?:^|[^\w/.-])((?:feat|fix|chore|refactor|perf|revert|style|cleanup|merge|diag|roadmap|wip|spike|investigation|guard|ci|backup|autoresearch)/([A-Za-z0-9][\w.-]*))(/?)`

file_extension_re := `\.[A-Za-z][A-Za-z0-9]{0,5}$`

release_please_re := `release-please--branches--[\w./-]+`

c_like_exts := {
	".rs", ".js", ".mjs", ".cjs", ".ts", ".c", ".h", ".cc", ".cpp", ".hpp", ".hlsl", ".hlsli",
	".wgsl", ".java", ".jsonc", ".typ", ".glsl", ".frag", ".vert",
}

hash_exts := {".py", ".sh", ".bash", ".rego", ".yml", ".yaml", ".toml"}

tool_input := object.get(input, "tool_input", {})

file_path := object.get(tool_input, "file_path", object.get(tool_input, "path", ""))

lower_tool_name := lower(object.get(input, "tool_name", ""))

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	marker := comment_marker
	not vendored
	some pair in written_pairs
	new := mentions(pair.new, marker)
	added := count(new) - count(mentions(pair.old, marker))
	added > 0

	decision := {
		"rule_id": "ER-EFFECTS-NO-TRACKER-REFS-GUARD",
		"severity": "MEDIUM",
		"reason": concat("", [
			"🧁 Cupcake stopped a write to ",
			file_path,
			" that adds ",
			format_int(added, 10),
			" beads id, pull request number or branch name to a comment: ",
			concat(", ", [concat("", ["`", m, "`"]) | some m in new]),
			".\n\nComments carry no beads ids, PR numbers or branch names (user request 2026-11-24); the history lives in git and bd. `scripts/check-no-tracker-refs.py` (check.sh, lint stage) holds every tracked file at zero, so this would go red at push time.",
			"\n\nHappy path: say what the code does and why, and leave out where the work was tracked. A measurement keeps its date and the command or log that produced it. If the text really is a branch name the code uses -- a test fixture's branch, a ref a script pushes -- it belongs in a string, and strings are not read.",
		]),
	}
}

# Each written text paired with the text it replaces. A Write replaces a file this guard cannot see.
written_pairs contains {"old": "", "new": object.get(tool_input, "content", "")} if {
	lower_tool_name == "write"
}

written_pairs contains {"old": object.get(tool_input, "old_string", ""), "new": object.get(tool_input, "new_string", "")} if {
	lower_tool_name == "edit"
}

written_pairs contains {"old": object.get(edit, "old_string", ""), "new": object.get(edit, "new_string", "")} if {
	lower_tool_name == "multiedit"
	some edit in object.get(tool_input, "edits", [])
}

comment_marker := "//" if {
	some ext in c_like_exts
	endswith(file_path, ext)
}

comment_marker := "#" if {
	some ext in hash_exts
	endswith(file_path, ext)
}

comment_marker := "--" if endswith(file_path, ".lua")

# Both spellings reach this guard: an absolute path from the harness, a relative one from a test.
vendored if contains(file_path, "/third_party/")

vendored if startswith(file_path, "third_party/")

vendored if contains(file_path, "/vendor/")

vendored if startswith(file_path, "vendor/")

comment_lines(text, marker) := [line |
	some line in split(text, "\n")
	startswith(trim_space(line), marker)
]

# Every mention on the comment lines of `text`, once per occurrence, as the matched text.
mentions(text, marker) := array.concat(
	array.concat(
		array.concat(simple(text, marker, bd_re), simple(text, marker, bsg_re)),
		array.concat(plain(text, marker, pr_word_re), plain(text, marker, pr_suffix_re)),
	),
	array.concat(
		array.concat(plain(text, marker, pr_url_re), bare_numbers(text, marker)),
		array.concat(branches(text, marker), plain(text, marker, release_please_re)),
	),
)

plain(text, marker, re) := [m[0] |
	some line in comment_lines(text, marker)
	some m in regex.find_all_string_submatch_n(re, line, -1)
]

# A beads id followed by a hyphen is part of a longer word, which `\b` alone lets through.
simple(text, marker, re) := [m[0] |
	some line in comment_lines(text, marker)
	some m in regex.find_all_string_submatch_n(re, line, -1)
	m[1] == ""
]

bare_numbers(text, marker) := [trim_space(m[0]) |
	some line in comment_lines(text, marker)
	some m in regex.find_all_string_submatch_n(pr_bare_re, line, -1)
	m[1] == ""
]

# A branch has a hyphenated slug, and is neither a file nor a directory on its way to one.
branches(text, marker) := [m[1] |
	some line in comment_lines(text, marker)
	some m in regex.find_all_string_submatch_n(branch_re, line, -1)
	contains(m[2], "-")
	not regex.match(file_extension_re, m[2])
	m[3] == ""
]
