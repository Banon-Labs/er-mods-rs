# METADATA
# scope: package
# title: The Agent Does Not Rewrite Session Transcripts
# authors: ["er-quickload agents"]
# custom:
#   severity: CRITICAL
#   id: ER-EFFECTS-TRANSCRIPT-WRITE-GUARD
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"]
package cupcake.policies.claude.transcript_write_guard

import rego.v1

# The push authorization (`git_block_any_push`, user directive 2026-10-07) is
# read from the session transcript under ~/.claude/projects: the user's own
# typed `authorize <sha>` reply. The reader ignores anything appended to that
# file -- it follows Claude Code's own `parentUuid` chain back from the pending
# tool call and refuses a transcript with a repeated uuid -- so the remaining way
# to forge an authorization is to rewrite lines already in it. This guard
# refuses the direct ways an agent would do that: the Write/Edit tools on a
# transcript, an in-place editor (`sed -i`, `perl -i`, `sponge`, `truncate`),
# a redirect or `tee` into the transcript directory, and a copy or move whose
# last argument lands there.
#
# It is a guard on the obvious routes, not a proof: the file belongs to the user
# the agent's shell runs as. Python writes are already refused repo-wide by
# `bash_no_python_file_write`. Reading transcripts stays allowed -- signals and
# agents both do it -- and so do writes to the auto-memory under the same tree,
# which are Markdown, not `.jsonl`.
#
# Written in string operations rather than regex on purpose: a regex added to a
# rule is a hazard to every policy at once (bd
# `a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14`).

transcript_dir := ".claude/projects/"

tool_input := object.get(input, "tool_input", {})

edited_path := object.get(tool_input, "file_path", object.get(tool_input, "notebook_path", ""))

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name in {"Write", "Edit", "MultiEdit", "NotebookEdit"}
	contains(edited_path, transcript_dir)
	endswith(edited_path, ".jsonl")

	decision := denial
}

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	bash_rewrites_transcript

	decision := denial
}

denial := {
	"rule_id": "ER-EFFECTS-TRANSCRIPT-WRITE-GUARD",
	"reason": "Session transcripts under ~/.claude/projects are Claude Code's record of the conversation, and the push authorization is read from them. The agent reads them; it never writes, edits, truncates, moves or copies over them.",
	"severity": "CRITICAL",
}

command := lower(object.get(tool_input, "command", ""))

names_transcripts if contains(command, transcript_dir)

in_place_editors := ["sed -i", "sed --in-place", "perl -i", "perl -pi", "sponge", "truncate "]

bash_rewrites_transcript if {
	names_transcripts
	some editor in in_place_editors
	contains(command, editor)
}

# `... > ~/.claude/projects/...`, `>>"$HOME/.claude/projects/...`, `tee -a /home/x/.claude/projects/...`:
# look at the words just before each occurrence of the directory.
bash_rewrites_transcript if {
	names_transcripts
	pieces := split(command, transcript_dir)
	some i in numbers.range(0, count(pieces) - 2)
	written_into(split(trim_right(pieces[i], " \t"), " "), pieces[i])
}

# A copy or move whose final argument is a transcript path.
bash_rewrites_transcript if {
	names_transcripts
	words := split(trim(command, " \t;&|"), " ")
	contains(words[count(words) - 1], transcript_dir)
	some mover in ["cp", "mv", "install", "rsync", "ln", "dd"]
	mover in words
}

# The path's own word ends with the text before the directory (`~/`, `"$home/`,
# `/home/x/`). It was redirected into when that word starts with `>`, or when
# the word before it is a redirect operator or `tee` (with or without options).
written_into(words, _) if {
	contains(words[count(words) - 1], ">")
}

written_into(words, before) if {
	not endswith(before, " ")
	count(words) >= 2
	endswith(words[count(words) - 2], ">")
}

written_into(words, before) if {
	endswith(before, " ")
	endswith(words[count(words) - 1], ">")
}

written_into(words, before) if {
	last := last_operand_before_path(words, before)
	some j, word in words
	word == "tee"
	j <= last
	non_options := [k |
		some k in numbers.range(j + 1, last)
		k > j
		k <= last
		not startswith(words[k], "-")
	]
	count(non_options) == 0
}

# Index of the last word that sits between a command and the path: when the
# path is glued to the word before it (`~/`, `/home/x/`), that word is the
# path's own head and does not count.
last_operand_before_path(words, before) := count(words) - 1 if endswith(before, " ")

last_operand_before_path(words, before) := count(words) - 2 if not endswith(before, " ")
