# OPA unit tests for no_source_edit_during_live_run.
#
# Not loaded by the cupcake engine (which scans .cupcake/policies/<harness>/
# and .cupcake/system/ only). Run with:
#   opa test .cupcake/policies/claude/no_source_edit_during_live_run.rego \
#            .cupcake/tests/no_source_edit_during_live_run_test.rego
package cupcake.policies.claude.no_source_edit_during_live_run_test

import rego.v1

import data.cupcake.policies.claude.no_source_edit_during_live_run as guard

RULE := "ER-EFFECTS-NO-SOURCE-EDIT-DURING-LIVE-RUN"

LIVE := "[er-sentinel] LIVE:\n  /home/banon/Elden/br-20260912-204637-08ba.me3"

edit_event(path, signal) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Edit",
	"tool_input": {"file_path": path},
	"signals": {"live_er_run": signal},
}

rule_ids(denials) := {d.rule_id | some d in denials}

denied(event) if {
	denials := guard.deny with input as event
	RULE in rule_ids(denials)
}

# --- a source edit during a live run is DENIED -------------------------------

# The exact shape that ran all session on 2026-09-12.
test_deny_core_edit_while_live if {
	denied(edit_event("/home/banon/projects/er-mods-rs/.worktrees/quickload-slim/crates/er-quit-menu-core/src/menu_pump.rs", LIVE))
}

test_deny_relative_crates_path_while_live if {
	denied(edit_event("crates/er-save-game-row/src/lib.rs", LIVE))
}

test_deny_write_tool_while_live if {
	denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Write",
		"tool_input": {"file_path": "crates/er-gfx/src/text_input_02_990.rs"},
		"signals": {"live_er_run": LIVE},
	})
}

# --- with no live run, editing is untouched ----------------------------------

test_allow_core_edit_with_no_live_run if {
	not denied(edit_event("crates/er-quit-menu-core/src/menu_pump.rs", ""))
}

# A signal that failed or timed out fails OPEN: no live run is the safe default,
# and a broken signal must not make the tree uneditable.
test_allow_when_signal_is_absent if {
	not denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Edit",
		"tool_input": {"file_path": "crates/er-quit-menu-core/src/menu_pump.rs"},
		"signals": {},
	})
}

test_allow_when_sentinel_reports_no_live_run if {
	not denied(edit_event("crates/er-quit-menu-core/src/menu_pump.rs", "[er-sentinel] no live run"))
}

# --- inert paths stay editable mid-run ---------------------------------------

# Writing this very policy while a run is up has to remain possible.
test_allow_policy_edit_while_live if {
	not denied(edit_event(".cupcake/policies/claude/no_source_edit_during_live_run.rego", LIVE))
}

test_allow_script_edit_while_live if {
	not denied(edit_event("scripts/er-run-branch.py", LIVE))
}

test_allow_docs_edit_while_live if {
	not denied(edit_event("docs/er-1.17-migration.md", LIVE))
}

# --- the guard stays out of everything else ----------------------------------

test_allow_bash_while_live if {
	not denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Bash",
		"tool_input": {"command": "cargo check"},
		"signals": {"live_er_run": LIVE},
	})
}

# --- closure scoping: only crates the live run actually loaded -----------------
#
# Added 2026-09-16 after the guard refused an edit its own PostToolUse classifier
# reports as skipped. The run was launched `--without er-invasion-warp`, so
# `scripts/er-stale-run-sentinel.sh classify crates/er-invasion-warp/src/...` said
# `SKIP crate-builds-no-loaded-dll`, and this policy denied the edit anyway because
# it matched on the `crates/` directory alone. The signal now carries the sentinel's
# `closure` output so both ends decide on the same set.

LIVE_WITH_CLOSURE := concat("\n", [
	LIVE,
	"CLOSURE crates/er-quickload",
	"CLOSURE crates/er-game-base",
	"CLOSURE crates/er-invasion-warp-core",
])

test_deny_crate_that_feeds_a_loaded_dll if {
	denied(edit_event("crates/er-quickload/src/lib.rs", LIVE_WITH_CLOSURE))
}

test_allow_crate_the_run_did_not_load if {
	not denied(edit_event("crates/er-invasion-warp/src/local_invasion_filter.rs", LIVE_WITH_CLOSURE))
}

# `er-invasion-warp-core` is in the closure and `er-invasion-warp` is not. A name
# match would confuse the two; the directory separator is what keeps them apart.
test_deny_the_core_crate_that_is_in_the_closure if {
	denied(edit_event("crates/er-invasion-warp-core/src/local_invasion.rs", LIVE_WITH_CLOSURE))
}

test_deny_absolute_path_inside_a_closure_crate if {
	denied(edit_event("/home/banon/projects/er-mods-rs/crates/er-game-base/src/mem.rs", LIVE_WITH_CLOSURE))
}

# Fail closed: a live run whose closure could not be computed is the case the coarse
# rule was right about, so an empty closure denies exactly as before.
test_deny_when_the_closure_is_empty if {
	denied(edit_event("crates/er-invasion-warp/src/local_invasion_filter.rs", LIVE))
}

test_scripts_stay_editable_with_a_closure if {
	not denied(edit_event("scripts/er-stale-run-sentinel.sh", LIVE_WITH_CLOSURE))
}

# --- the sentinel's verdict decides when the signal carries one ----------------
#
# Added 2026-10-02. The sentinel tears down for files a crate's build reads, and
# this policy matched `crates/` alone while its refusal text promised docs stay
# editable, so an edit to a build-read doc was allowed and then killed the run.
# The signal now carries `er-stale-run-sentinel.sh verdict <path>` and the
# policy denies exactly its TEARDOWN. The verdict lines below are real output of
# that mode against the live ~/Elden/r3-view.me3 run.

PROFILE := "/home/banon/Elden/r3-view.me3"

verdict_signal(line) := concat("\n", [LIVE, concat("", ["VERDICT ", line])])

TEARDOWN_CRATE := concat("\t", ["TEARDOWN", "crate-feeds-loaded-dll", "pkg=er-game-base feeds er_build_watermark.dll,er_r3_view.dll", PROFILE])

TEARDOWN_BUILD_INPUT := concat("\t", ["TEARDOWN", "build-input-feeds-loaded-dll", "read by the build of pkg=er-game-base, which feeds er_build_watermark.dll,er_r3_view.dll", PROFILE])

SKIP_PROSE := concat("\t", ["SKIP", "inert-documentation", "AGENTS.md is prose outside every crate and no build reads it", PROFILE])

SKIP_IGNORED := concat("\t", ["SKIP", "not-repo-source", "outside the repo or gitignored", ""])

SKIP_UNLOADED_CRATE := concat("\t", ["SKIP", "crate-builds-no-loaded-dll", "pkg=er-invasion-warp is not in the dependency closure of er_r3_view", PROFILE])

test_deny_crates_file_on_teardown_verdict if {
	denied(edit_event("/home/banon/projects/er-mods-rs/crates/er-game-base/src/mem.rs", verdict_signal(TEARDOWN_CRATE)))
}

test_allow_agents_md_on_skip_verdict if {
	not denied(edit_event("/home/banon/projects/er-mods-rs/AGENTS.md", verdict_signal(SKIP_PROSE)))
}

# The case this section exists for: a doc outside `crates/` that
# `er-game-base/build.rs` compiles into the loaded DLLs.
test_deny_build_read_rva_map_while_live if {
	denied(edit_event("/home/banon/projects/er-mods-rs/docs/recon/rva-map-1162-to-1170.data.tsv", verdict_signal(TEARDOWN_BUILD_INPUT)))
}

test_allow_build_read_rva_map_with_no_live_run if {
	not denied(edit_event("/home/banon/projects/er-mods-rs/docs/recon/rva-map-1162-to-1170.data.tsv", ""))
}

# `functions.tsv` is named only in comments and is gitignored; the sentinel skips
# it, so the policy must too.
test_allow_unread_gitignored_rva_map_while_live if {
	not denied(edit_event("/home/banon/projects/er-mods-rs/docs/recon/rva-map-1162-to-1170.functions.tsv", verdict_signal(SKIP_IGNORED)))
}

# A SKIP verdict overrides the closure fallback for a crate the run did not load,
# even with no CLOSURE lines (which alone would fail closed).
test_allow_unloaded_crate_on_skip_verdict if {
	not denied(edit_event("crates/er-invasion-warp/src/local_invasion_filter.rs", verdict_signal(SKIP_UNLOADED_CRATE)))
}

test_refusal_names_the_sentinel_branch if {
	denials := guard.deny with input as edit_event("docs/recon/rva-map-1162-to-1170.data.tsv", verdict_signal(TEARDOWN_BUILD_INPUT))
	some d in denials
	contains(d.reason, "build-input-feeds-loaded-dll")
	not contains(d.reason, "docs stay editable")
}
