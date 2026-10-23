# OPA unit tests for edit_no_comment_caps_guard.
#
# Run with:
#   opa test .cupcake/policies/claude/edit_no_comment_caps_guard.rego \
#            .cupcake/tests/edit_no_comment_caps_guard_test.rego
#
# The negative cases are the load-bearing half. This guard refuses an edit, so a false positive
# costs the author a write they cannot make; every shape it must NOT fire on is pinned below.
package cupcake.policies.claude.edit_no_comment_caps_guard_test

import rego.v1

import data.cupcake.policies.claude.edit_no_comment_caps_guard as guard

write_event(path, content) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Write",
	"tool_input": {"file_path": path, "content": content},
}

edit_event(path, new_string) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Edit",
	"tool_input": {"file_path": path, "old_string": "x", "new_string": new_string},
}

multiedit_event(path, strings) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "MultiEdit",
	"tool_input": {
		"file_path": path,
		"edits": [{"old_string": "x", "new_string": s} | some s in strings],
	},
}

rule_ids(denials) := {d.rule_id | some d in denials}

denied(event) if {
	denials := guard.deny with input as event
	"ER-EFFECTS-COMMENT-CAPS-GUARD" in rule_ids(denials)
}

# --- it fires on shouting, in every authoring tool -------------------------------------------

test_deny_rust_line_comment if {
	denied(write_event("crates/x/src/lib.rs", "// this is NOT the same pointer\n"))
}

test_deny_rust_doc_comment if {
	denied(write_event("crates/x/src/lib.rs", "/// the ONLY writer of the field\n"))
}

test_deny_python_comment if {
	denied(write_event("scripts/x.py", "# EVERY caller must hold the lock\n"))
}

test_deny_shell_comment if {
	denied(write_event("scripts/x.sh", "# BEFORE the launch, check the profile\n"))
}

test_deny_edit_new_string if {
	denied(edit_event("crates/x/src/lib.rs", "// it is ALWAYS installed\n"))
}

test_deny_multiedit_second_edit if {
	denied(multiedit_event("crates/x/src/lib.rs", ["// fine\n", "// it is NEVER installed\n"]))
}

test_deny_line_buried_in_a_longer_write if {
	denied(write_event(
		"crates/x/src/lib.rs",
		"fn f() {}\n\n// a paragraph that runs\n// on for a WHILE and then shouts THIS\nfn g() {}\n",
	))
}

test_reason_names_the_file_and_the_line if {
	denials := guard.deny with input as write_event("crates/x/src/lib.rs", "// the ONLY writer\n")
	some d in denials
	contains(d.reason, "crates/x/src/lib.rs")
	contains(d.reason, "// the ONLY writer")
}

# The fence is honoured, not the whole edit waved through: prose either side of it is still read.
test_deny_shouted_word_after_a_fence_closes if {
	denied(edit_event("crates/x/src/lib.rs", concat("\n", [
		"//! ```text",
		"//! 140c59277: 48 8b 4f 08     MOV RCX, [RDI + 0x8]      ; free the node -- on BOTH paths",
		"//! ```",
		"//!",
		"//! That byte is the ONLY flag.",
		"",
	])))
}

test_deny_shouted_word_before_a_fence_opens if {
	denied(edit_event("crates/x/src/lib.rs", concat("\n", [
		"/// This is NOT the same pointer:",
		"/// ```text",
		"/// mov rax, [rcx]   ; THE pointer",
		"/// ```",
		"",
	])))
}

test_reason_says_to_widen_an_edit_to_its_fence if {
	denials := guard.deny with input as edit_event("crates/x/src/lib.rs", "//! mov rax, rcx ; on BOTH paths\n")
	some d in denials
	contains(d.reason, "widen the edit")
}

# The gate's `#` comment stream never toggles on a fence, so neither does this guard.
test_deny_python_comment_between_fence_lines if {
	denied(write_event("scripts/x.py", "# ```text\n# THE only writer\n# ```\n"))
}

# An edit is judged on the shouted words it adds. A second copy of a word is still an addition.
test_deny_edit_that_adds_another_copy_of_a_word if {
	denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Edit",
		"tool_input": {
			"file_path": "crates/x/src/lib.rs",
			"old_string": "// it is NOT live\n",
			"new_string": "// it is NOT live\n// and NOT safe either\n",
		},
	})
}

# --- and NOT on any of these ------------------------------------------------------------------

test_allow_lowercase_prose if {
	not denied(write_event("crates/x/src/lib.rs", "// this is not the same pointer\n"))
}

test_allow_backticked_name if {
	not denied(write_event("crates/x/src/lib.rs", "// the x86 `NOT` instruction, quoted\n"))
}

test_allow_quoted_status_string if {
	not denied(write_event("scripts/x.sh", "# prints \"NOT RUN\" and exits\n"))
}

test_allow_acronyms if {
	not denied(write_event("crates/x/src/lib.rs", "// the DLL exports RVA 0x140 via its ABI\n"))
}

test_allow_x86_mnemonics if {
	not denied(write_event("crates/x/src/lib.rs", "// AND RAX, RCX then CALL the thunk; TEST and PUSH follow\n"))
}

test_allow_underscored_symbol if {
	not denied(write_event("crates/x/src/lib.rs", "// SAVE_OWNS_THE_SLOT is set by the caller\n"))
}

test_allow_code_line_that_is_not_a_comment if {
	not denied(write_event("crates/x/src/lib.rs", "let s = \"THE ONLY WRITER\";\n"))
}

test_allow_generated_source_inside_a_string_literal if {
	not denied(write_event("crates/x/build.rs", "    \"// TWO tables, because THIS is generated\\n\\\n"))
}

# Fenced code in a Rust comment is code: the gate's `FENCE` toggle drops it, so this guard must
# too. The listing is the one in `freelist_shutdown_assert.rs` that this guard refused on
# 2026-10-23.
test_allow_edit_with_a_fenced_listing_in_a_module_doc if {
	not denied(edit_event("crates/er-seamless-bugfixes/src/patches/freelist_shutdown_assert.rs", concat("\n", [
		"//! `+0x20` and breaks on any node whose byte at `+0x18` is still set:",
		"//!",
		"//! ```text",
		"//! 140c59270: 80 7a 18 00     CMP byte ptr [RDX + 0x18], 0x0",
		"//! 140c59274: 74 01           JZ  0x140c59277",
		"//! 140c59276: cc              INT3",
		"//! 140c59277: 48 8b 4f 08     MOV RCX, [RDI + 0x8]      ; free the node -- on BOTH paths",
		"//! ```",
		"//!",
		"//! That byte is the registrar's flag.",
		"",
	])))
}

test_allow_write_with_a_fenced_block_in_an_item_doc if {
	not denied(write_event("crates/x/src/lib.rs", concat("\n", [
		"/// Reads the field.",
		"///",
		"/// ```text",
		"/// lea rax, [rcx+0x60] ; ret   -> THIS FIELD, callees 1 and 6",
		"/// ```",
		"fn f() {}",
		"",
	])))
}

test_allow_multiedit_with_a_fenced_block if {
	not denied(multiedit_event("crates/x/src/lib.rs", [
		"// fine\n",
		"    // ```text\n    // d8 == 2 -> ELSE END THE SESSION\n    // ```\n",
	]))
}

# The gate drops everything after a fence that never closes, and so does this guard.
test_allow_lines_after_an_unclosed_fence if {
	not denied(write_event("crates/x/src/lib.rs", "//! ```text\n//! forwarded confirms == THE TOTAL\n"))
}

# A shouted word the edit carries over from `old_string` is not new writing. The port to 2.7.2.0
# rewrote every address of the listing above without its fence lines, and kept the annotation.
test_allow_edit_that_rewrites_a_line_and_keeps_its_shouted_word if {
	not denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Edit",
		"tool_input": {
			"file_path": "crates/er-seamless-bugfixes/src/patches/freelist_shutdown_assert.rs",
			"old_string": "//! 140c57677: 48 8b 4f 08     MOV RCX, [RDI + 0x8]      ; free the node -- on BOTH paths\n",
			"new_string": "//! 140c59277: 48 8b 4f 08     MOV RCX, [RDI + 0x8]      ; free the node -- on BOTH paths\n",
		},
	})
}

# The contract text the gate's baseline records stays editable around.
test_allow_edit_next_to_baseline_contract_text if {
	not denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Edit",
		"tool_input": {
			"file_path": "crates/x/src/lib.rs",
			"old_string": "    // do NOT call TitleTopDialog::open_menu from this game-task\n    tick();\n",
			"new_string": "    // do NOT call TitleTopDialog::open_menu from this game-task\n    tick_twice();\n",
		},
	})
}

test_allow_vendored_source if {
	not denied(write_event("third_party/hudhook/src/util.rs", "// this is NOT the same pointer\n"))
}

test_allow_unscanned_file_type if {
	not denied(write_event("docs/notes.md", "// this is NOT the same pointer\n"))
}

test_allow_other_events if {
	not denied({
		"hook_event_name": "PostToolUse",
		"tool_name": "Write",
		"tool_input": {"file_path": "crates/x/src/lib.rs", "content": "// it is NOT live\n"},
	})
}

test_allow_read_tool if {
	not denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Read",
		"tool_input": {"file_path": "crates/x/src/lib.rs"},
	})
}
