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
