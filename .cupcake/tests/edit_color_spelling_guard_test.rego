# OPA unit tests for edit_color_spelling_guard.
#
# Run with:
#   opa test .cupcake/policies/claude/edit_color_spelling_guard.rego \
#            .cupcake/tests/edit_color_spelling_guard_test.rego
#
# The British spelling is assembled from two halves below, so this file carries none of what the
# gate counts. The negative cases are the load-bearing half: a false
# positive costs the author an edit they cannot make.
package cupcake.policies.claude.edit_color_spelling_guard_test

import rego.v1

import data.cupcake.policies.claude.edit_color_spelling_guard as guard

word := concat("", ["col", "our"])

title_word := concat("", ["Col", "our"])

edit_event(path, old, new) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Edit",
	"tool_input": {"file_path": path, "old_string": old, "new_string": new},
}

multiedit_event(path, pairs) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "MultiEdit",
	"tool_input": {
		"file_path": path,
		"edits": [{"old_string": p[0], "new_string": p[1]} | some p in pairs],
	},
}

write_event(path, content) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Write",
	"tool_input": {"file_path": path, "content": content},
}

denied(event) if {
	denials := guard.deny with input as event
	"ER-EFFECTS-COLOR-SPELLING-GUARD" in {d.rule_id | some d in denials}
}

# --- it fires when an edit adds one ------------------------------------------------------------

test_deny_added_in_a_comment if {
	denied(edit_event("crates/x/src/lib.rs", "// the tint\n", concat("", ["// the ", word, " tint\n"])))
}

test_deny_added_inside_an_identifier if {
	denied(edit_event("crates/x/src/lib.rs", "let a = 1;", concat("", ["let name_", word, " = 1;"])))
}

test_deny_capitalised_and_shouted if {
	denied(edit_event("scripts/x.py", "x", concat("", ["# ", upper(word), " and ", title_word, "Table"])))
}

test_deny_one_more_than_the_edit_removed if {
	old := concat("", ["// one ", word])
	denied(edit_event("README.md", old, concat("", [old, " and another ", word])))
}

test_deny_multiedit_when_any_edit_adds_one if {
	denied(multiedit_event("crates/x/src/lib.rs", [["a", "b"], ["c", concat("", ["c ", word])]]))
}

test_deny_reason_names_the_count_and_the_fix if {
	denials := guard.deny with input as edit_event("a.rs", "x", concat("", [word, " ", word]))
	some d in denials
	contains(d.reason, "adds 2 British")
	contains(d.reason, "write `color`")
}

# --- it stays quiet on everything else ---------------------------------------------------------

test_allow_color if {
	not denied(edit_event("crates/x/src/lib.rs", "x", "// the color and ColorTable\n"))
}

test_allow_an_edit_that_keeps_the_count if {
	line := concat("", ["// a ", word, " here"])
	not denied(edit_event("crates/x/src/lib.rs", line, concat("", [line, " and moved"])))
}

test_allow_an_edit_that_removes_one if {
	not denied(edit_event("crates/x/src/lib.rs", concat("", ["// ", word]), "// color"))
}

test_allow_a_quoted_key if {
	not denied(edit_event("scripts/x.py", "x", concat("", ["doc[\"skin", title_word, "\"] = rgb"])))
}

test_allow_a_json_key_and_value if {
	not denied(edit_event("data/x.json", "x", concat("", ["\"type\": \"", word, "\""])))
}

test_allow_write_which_this_guard_does_not_judge if {
	not denied(write_event("crates/x/src/lib.rs", concat("", ["// ", word])))
}

test_allow_cargo_lock if {
	not denied(edit_event("Cargo.lock", "x", concat("", ["name = ", word])))
}

test_allow_post_tool_use if {
	event := object.union(edit_event("a.rs", "x", word), {"hook_event_name": "PostToolUse"})
	not denied(event)
}

test_the_policy_spells_its_pattern_without_the_word if {
	not contains(guard.banned_re, word)
	regex.match(guard.banned_re, word)
}
