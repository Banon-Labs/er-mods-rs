# OPA unit tests for edit_no_tracker_refs_guard.
#
# Run with:
#   opa test .cupcake/policies/claude/edit_no_tracker_refs_guard.rego \
#            .cupcake/tests/edit_no_tracker_refs_guard_test.rego
#
# The beads prefix is assembled from halves below, so this file's comments carry none of what the
# gate reads. The negative cases are the load-bearing half: a false positive costs the author an
# edit they cannot make. They are the false positives the gate measured over the tree.
package cupcake.policies.claude.edit_no_tracker_refs_guard_test

import rego.v1

import data.cupcake.policies.claude.edit_no_tracker_refs_guard as guard

bd := concat("", ["er-effects", "-rs"])

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
	"ER-EFFECTS-NO-TRACKER-REFS-GUARD" in {d.rule_id | some d in denials}
}

added(path, comment) := edit_event(path, "fn f() {}\n", concat("", [comment, "\nfn f() {}\n"]))

# --- it fires when an edit adds one ------------------------------------------------------------

test_deny_beads_id_in_a_rust_comment if {
	denied(added("crates/x/src/lib.rs", concat("", ["// see ", bd, "-a1b2 for the run"])))
}

test_deny_three_character_beads_id if {
	denied(added("crates/x/src/lib.rs", concat("", ["//! (", bd, "-wmw)"])))
}

test_deny_child_beads_id if {
	denied(added("scripts/x.py", concat("", ["# ", bd, "-a1b2.3 defect"])))
}

test_deny_backseat_gamer_id if {
	denied(added("scripts/x.sh", "# filed as bsg-chy"))
}

test_deny_pr_number if {
	denied(added("scripts/x.py", "# landed in PR #398"))
}

test_deny_pr_without_hash_and_glued if {
	denied(added("crates/x/src/lib.rs", "// PR 12 and the PR-#103 skip"))
}

test_deny_pull_request_number if {
	denied(added(".cupcake/policies/claude/x.rego", "# opened draft pull request #481"))
}

test_deny_squash_suffix if {
	denied(added("scripts/x.js", "// feat(x): thing (#62)"))
}

test_deny_pull_link if {
	denied(added("crates/x/src/lib.rs", "// https://github.com/o/r/pull/5"))
}

test_deny_bare_three_digit_number if {
	denied(added("crates/x/src/lib.rs", "// the #362 merge"))
}

test_deny_branch if {
	denied(added("crates/x/src/lib.rs", "//! a port of the lab on `feat/ai-lab-turtles`"))
}

test_deny_fix_branch_in_yaml if {
	denied(added(".github/workflows/x.yml", "# pushed fix/harness-repl-reach today"))
}

test_deny_release_please_branch if {
	denied(added(".github/workflows/x.yml", "# release-please--branches--main"))
}

test_deny_lua_comment if {
	denied(added("scripts/frida/ai-lua/x.lua", "-- see PR #398"))
}

test_deny_write_that_creates_one if {
	denied(write_event("scripts/new.py", "# from PR #398\ndef f():\n    pass\n"))
}

test_deny_one_more_than_the_edit_removed if {
	old := "// PR #398\n"
	denied(edit_event("crates/x/src/lib.rs", old, concat("", [old, "// and PR #399\n"])))
}

test_deny_an_edit_that_swaps_one_mention_for_another if {
	denied(edit_event("crates/x/src/lib.rs", "// PR #398\n", "// PR #399\n"))
}

test_deny_a_swap_reason_names_only_the_new_one if {
	denials := guard.deny with input as edit_event("scripts/x.py", "# PR #398\n", "# PR #399\n")
	some d in denials
	contains(d.reason, "adds 1")
	contains(d.reason, "`PR #399`")
	not contains(d.reason, "`PR #398`")
}

test_deny_a_beads_swap_of_equal_count if {
	old := concat("", ["// ", bd, "-a1b2 and fix/load-x-y\n"])
	new := concat("", ["// ", bd, "-c3d4 and fix/load-x-y\n"])
	denied(edit_event("crates/x/src/lib.rs", old, new))
}

test_deny_a_beads_id_run_on_into_a_word if {
	denied(added("crates/x/src/lib.rs", concat("", ["// the ", bd, "-j3r-style table guard"])))
}

test_deny_a_backseat_gamer_id_run_on_into_a_word if {
	denied(added("scripts/x.py", "# the bsg-chy-era limit"))
}

test_deny_a_branch_after_origin if {
	denied(added("scripts/x.py", "# on `origin/feat/build-kit-consumables-crit-row` and not on main"))
}

test_deny_a_branch_after_refs_heads if {
	denied(added(".cupcake/policies/claude/x.rego", "#     git push origin origin/x:refs/heads/split/drop-dead-gates"))
}

test_deny_the_merge_history_prefixes if {
	every prefix in ["feature", "split", "promote", "stack", "rename", "crate", "tooling", "salvage", "recreate", "gate"] {
		denied(added("crates/x/src/lib.rs", concat("", ["// landed from ", prefix, "/some-work-here"])))
	}
}

test_deny_a_hash_comment_in_a_txt_allowlist if {
	denied(added("scripts/oracle-writers-allowlist.txt", concat("", ["# known debt, tracked by ", bd, "-56fx"])))
}

test_allow_a_txt_data_line if {
	not denied(added("scripts/oracle-writers-allowlist.txt", concat("", ["NAME_", bd, "-56fx"])))
}

test_allow_an_edit_that_repeats_a_mention_it_keeps_no_more_often if {
	old := "// PR #398\n// PR #398\n"
	not denied(edit_event("crates/x/src/lib.rs", old, "// PR #398\nfn f() {}\n// PR #398\n"))
}

test_deny_multiedit_when_any_edit_adds_one if {
	denied(multiedit_event("crates/x/src/lib.rs", [["a", "b"], ["c", "c\n// PR #398"]]))
}

test_deny_reason_names_the_mention_and_the_rule if {
	denials := guard.deny with input as added("scripts/x.py", "# PR #398 and fix/load-x-y")
	some d in denials
	contains(d.reason, "adds 2")
	contains(d.reason, "`PR #398`")
	contains(d.reason, "`fix/load-x-y`")
	contains(d.reason, "Comments carry no beads ids, PR numbers or branch names (user request 2026-11-24)")
	contains(d.reason, "git and bd")
}

# --- it stays quiet on everything else ---------------------------------------------------------

test_allow_paths_that_share_a_prefix if {
	not denied(added("crates/x/src/lib.rs", "// docs/agents/runtime-evidence.md, docs/ci-gate-portability.tsv, test/fixtures/x.json, ci/run-all.sh, build/out-dir"))
}

test_allow_branch_shaped_file_and_dir if {
	not denied(added("crates/x/src/lib.rs", "// crates/fix/load-thing.rs, feat/load-thing.rs, feat/load-thing/mod.rs"))
}

test_allow_prose_with_a_slash if {
	not denied(added("crates/x/src/lib.rs", "// a fix/instrument run, the refactor/move gate, cleanup/destructor"))
}

test_allow_ordinals_and_counters if {
	not denied(added("scripts/x.py", "# the R1 #1 hit, run #14, CALL #13800, hit #370, kick #200"))
}

test_allow_a_list_of_counters if {
	not denied(added("crates/x/src/lib.rs", "// lines at #1,#2,#64,#128,#256,#512"))
}

test_allow_hex_colors_and_material_tags if {
	not denied(added("scripts/x.py", "# `#111216` at 0.94, #808080808080 gray, `#27#_Cloth`"))
}

test_allow_a_counter_quoted_from_a_log if {
	not denied(added("crates/x/src/lib.rs", "/// `cancelled rejected match (#1) -- ...`"))
}

test_allow_run_ids_and_profile_names if {
	not denied(added("crates/x/src/lib.rs", "// run pr109-boot-oscancel-20260730, /home/u/Elden/pr190-invasion-warp.me3"))
}

test_allow_words_that_are_not_ids if {
	not denied(added("scripts/x.py", concat("", ["# bsg-check, bsg-work, frida-up, ai-lab, ", bd, "-toolong"])))
}

test_allow_a_run_on_word_that_is_a_path_segment if {
	not denied(added("scripts/x.py", concat("", ["# holds /run/user/1000/", bd, "-check-sh.lock and ~/", bd, "-main-compare"])))
}

test_allow_a_run_on_word_that_is_a_file_name if {
	not denied(added("scripts/x.py", concat("", ["# writes ", bd, "-check-stages/x.log and ", bd, "-build-out.txt"])))
}

test_allow_an_upstream_issue_link if {
	not denied(added("crates/x/src/lib.rs", "// https://github.com/rust-lang/rust/issues/123"))
}

test_allow_placeholders if {
	not denied(added("crates/x/src/lib.rs", "// PR #<n>, (#<n>), <prefix>/<slug>"))
}

test_allow_a_branch_in_a_string if {
	not denied(added("scripts/x.sh", "git push origin fix/test-fixture-branch"))
}

test_allow_a_branch_in_a_rust_string if {
	not denied(added("crates/x/src/lib.rs", "let b = \"feat/fixture-branch\"; let p = \"PR #398\";"))
}

test_allow_markdown if {
	not denied(added("docs/agents/x.md", "# PR #398 on fix/harness-repl-reach"))
}

test_allow_vendored if {
	not denied(added("third_party/x/lib.rs", "// PR #398"))
}

test_allow_an_edit_that_keeps_the_count if {
	line := "// PR #398\n"
	not denied(edit_event("crates/x/src/lib.rs", line, concat("", [line, "fn moved() {}\n"])))
}

test_allow_an_edit_that_removes_one if {
	not denied(edit_event("crates/x/src/lib.rs", "// PR #398\n", "// the loader\n"))
}

test_allow_post_tool_use if {
	event := object.union(added("scripts/x.py", "# PR #398"), {"hook_event_name": "PostToolUse"})
	not denied(event)
}

test_the_policy_spells_its_prefix_without_the_word if {
	not contains(guard.bd_re, bd)
	regex.match(guard.bd_re, concat("", [bd, "-a1b2"]))
}
