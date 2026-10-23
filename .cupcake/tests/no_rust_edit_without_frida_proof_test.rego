# OPA unit tests for no_rust_edit_without_frida_proof.
#
# Not loaded by the cupcake engine (which scans .cupcake/policies/<harness>/
# and .cupcake/system/ only). Run with:
#   opa test .cupcake/policies/claude/no_rust_edit_without_frida_proof.rego \
#            .cupcake/tests/no_rust_edit_without_frida_proof_test.rego
#
# The rule under test is the executable half of the AGENTS.md line "The order is
# Frida, then Frida, then Frida, and only then a DLL". It denies a Write/Edit to
# `crates/**/*.rs` unless the `frida_evidence` signal opens with `PROVEN`.
#
# Every test below that asserts a denial is asserting a fail-closed path: absent
# signal, empty signal, missing signals object, `UNPROVEN` verdict. That direction
# is the whole design -- the opposite of `no_source_edit_during_live_run`, which
# fails open because a broken signal there means "no run to protect".
package cupcake.policies.claude.no_rust_edit_without_frida_proof_test

import rego.v1

import data.cupcake.policies.claude.no_rust_edit_without_frida_proof as guard

RULE := "ER-EFFECTS-NO-RUST-EDIT-WITHOUT-FRIDA-PROOF"

# The two verdict lines `scripts/er-frida-evidence.py --check` can print, copied
# from its own format strings so a change to the wording breaks a test here.
PROVEN := "PROVEN agent=scripts/frida/ersc-session.js pid=388 messages=12 seconds=41.5"

UNPROVEN_NOTHING := "UNPROVEN no-frida-evidence nothing has attached to the game and reported back"

UNPROVEN_SILENT := "UNPROVEN silent-session the last watch on scripts/frida/x.js reported 0 messages, so it observed nothing"

UNPROVEN_SPENT := "UNPROVEN spent-by-commit the last measurement predates a Rust commit to the crate being edited, so it belongs to a change that is already committed"

edit_event(path, signal) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Edit",
	"tool_input": {"file_path": path},
	"signals": {"frida_evidence": signal},
}

rule_ids(denials) := {d.rule_id | some d in denials}

denied(event) if {
	denials := guard.deny with input as event
	RULE in rule_ids(denials)
}

# --- a Rust edit with no measurement behind it is DENIED ----------------------

test_deny_relative_crates_path_with_no_signal_key if {
	denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Edit",
		"tool_input": {"file_path": "crates/er-invasion-warp/src/local_invasion_filter.rs"},
		"signals": {},
	})
}

# The engine is expected to attach `signals` for a policy that declares
# `required_signals`, but a rule that only denies when it is handed its evidence
# reader's output is not a gate. No `signals` object at all still denies.
test_deny_when_the_signals_object_is_missing_entirely if {
	denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Edit",
		"tool_input": {"file_path": "crates/er-invasion-warp/src/local_invasion_filter.rs"},
	})
}

# An empty string is what a timed-out or crashed reader produces, and it is the
# case the signal script's comment calls out by name.
test_deny_on_an_empty_signal if {
	denied(edit_event("crates/er-quickload/src/lib.rs", ""))
}

# --- the signal command itself failing ----------------------------------------
#
# A signal whose command exits non-zero never reaches the policy as its output:
# cupcake substitutes this record. Every test above hands the rule a string, which
# is how the live gate sat inert on 2026-09-16 with `opa test` fully green -- the
# script was not executable, an auto-discovered signal is exec'd directly rather
# than through `bash`, and `cupcake eval` allowed a `crates/` edit on exit code 126.

FAILURE_RECORD := {
	"error": "signal command failed",
	"exit_code": 126,
	"output": "",
	"success": false,
}

test_deny_when_the_signal_command_failed if {
	denied(edit_event("crates/er-quickload/src/lib.rs", FAILURE_RECORD))
}

test_deny_on_the_exact_126_record_that_was_measured if {
	denied(edit_event(
		"crates/er-invasion-warp/src/local_invasion_filter.rs",
		{"error": "Permission denied (os error 13)", "exit_code": 126, "output": "", "success": false},
	))
}

# The refusal has to say the reader broke, because "go and measure" is the wrong
# instruction when the evidence path is what is broken.
test_reason_names_a_broken_signal_rather_than_a_missing_measurement if {
	some decision in guard.deny with input as edit_event("crates/er-quickload/src/lib.rs", FAILURE_RECORD)
	decision.rule_id == RULE
	contains(decision.reason, "frida_evidence signal failed")
	contains(decision.reason, "executable")
}

# Any other non-string is the same class and denies the same way.
test_deny_when_the_signal_is_a_number if {
	denied(edit_event("crates/er-quickload/src/lib.rs", 126))
}

test_deny_when_the_signal_is_null if {
	denied(edit_event("crates/er-quickload/src/lib.rs", null))
}

# A failure record whose `output` happens to contain the verdict is still a failed
# signal. Reading a word out of the wreckage is how a broken gate looks open.
test_a_failure_record_carrying_proven_text_still_denies if {
	denied(edit_event(
		"crates/er-quickload/src/lib.rs",
		{"error": "timed out", "exit_code": 124, "output": "PROVEN agent=x messages=9", "success": false},
	))
}

test_deny_on_unproven_nothing_recorded if {
	denied(edit_event("crates/er-quickload/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_on_unproven_silent_session if {
	denied(edit_event("crates/er-quickload/src/lib.rs", UNPROVEN_SILENT))
}

test_deny_on_unproven_spent_by_commit if {
	denied(edit_event("crates/er-quickload/src/lib.rs", UNPROVEN_SPENT))
}

# `UNPROVEN` contains `PROVEN`; only a line that STARTS with it opens the gate.
test_unproven_is_not_read_as_proven if {
	denied(edit_event("crates/er-game-base/src/mem.rs", "UNPROVEN"))
}

test_deny_absolute_path_under_crates if {
	denied(edit_event("/home/banon/projects/er-mods-rs/crates/er-game-base/src/mem.rs", ""))
}

# An agent worktree puts the repo somewhere else entirely; the `/crates/` segment
# is what carries, not the repo root.
test_deny_worktree_path_under_crates if {
	denied(edit_event(
		"/home/banon/projects/er-mods-rs/.worktrees/quickload-slim/crates/er-quit-menu-core/src/menu_pump.rs",
		UNPROVEN_NOTHING,
	))
}

test_deny_write_tool_too if {
	denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Write",
		"tool_input": {"file_path": "crates/er-gfx/src/text_input_02_990.rs"},
		"signals": {"frida_evidence": UNPROVEN_NOTHING},
	})
}

test_deny_multiedit_tool_too if {
	denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "MultiEdit",
		"tool_input": {"file_path": "crates/er-hook/src/lib.rs"},
		"signals": {"frida_evidence": ""},
	})
}

# --- a measured session opens the gate ----------------------------------------

test_allow_crates_edit_when_proven if {
	not denied(edit_event("crates/er-invasion-warp/src/local_invasion_filter.rs", PROVEN))
}

test_allow_absolute_crates_edit_when_proven if {
	not denied(edit_event("/home/banon/projects/er-mods-rs/crates/er-quickload/src/lib.rs", PROVEN))
}

test_allow_write_tool_when_proven if {
	not denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Write",
		"tool_input": {"file_path": "crates/er-game-base/src/mem.rs"},
		"signals": {"frida_evidence": PROVEN},
	})
}

# The reader prints one line, but a signal that arrives with a trailing newline
# must still read as proof rather than as a broken reader.
test_allow_when_the_verdict_carries_a_trailing_newline if {
	not denied(edit_event("crates/er-quickload/src/lib.rs", concat("", [PROVEN, "\n"])))
}

# --- non-Rust files are never touched, proof or no proof ----------------------

test_allow_toml_with_no_proof if {
	not denied(edit_event("crates/er-invasion-warp/Cargo.toml", ""))
}

test_allow_workspace_toml_with_no_proof if {
	not denied(edit_event("Cargo.toml", UNPROVEN_NOTHING))
}

test_allow_markdown_with_no_proof if {
	not denied(edit_event("AGENTS.md", ""))
}

test_allow_markdown_under_crates_with_no_proof if {
	not denied(edit_event("crates/er-invasion-warp/README.md", UNPROVEN_SILENT))
}

test_allow_script_with_no_proof if {
	not denied(edit_event("scripts/er-frida-evidence.py", ""))
}

test_allow_frida_agent_file_with_no_proof if {
	not denied(edit_event("scripts/frida/ersc-session.js", UNPROVEN_NOTHING))
}

# Writing this very gate, and its tests, has to stay possible with no proof at
# all -- otherwise the first UNPROVEN verdict locks the gate's own repair shut.
test_allow_this_policy_with_no_proof if {
	not denied(edit_event(".cupcake/policies/claude/no_rust_edit_without_frida_proof.rego", ""))
}

test_allow_this_test_file_with_no_proof if {
	not denied(edit_event(".cupcake/tests/no_rust_edit_without_frida_proof_test.rego", ""))
}

test_allow_the_signal_script_with_no_proof if {
	not denied(edit_event(".cupcake/signals/frida_evidence.sh", ""))
}

test_allow_json_data_with_no_proof if {
	not denied(edit_event("data/effects.json", ""))
}

# Rust outside `crates/` is out of scope by construction: the host-side tools and
# the build-support scripts do not compile into anything the game loads.
test_allow_rust_outside_crates if {
	not denied(edit_event("tools/er-param-inspect/src/main.rs", UNPROVEN_NOTHING))
}

test_allow_build_support_rust if {
	not denied(edit_event("build-support/prologue_build.rs", ""))
}

# --- the guard stays out of everything else -----------------------------------

test_allow_bash_with_no_proof if {
	not denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Bash",
		"tool_input": {"command": "cargo xwin build --release --target x86_64-pc-windows-msvc"},
		"signals": {"frida_evidence": ""},
	})
}

# An unlisted tool carrying a crate path is still refused, which is the half of the old
# routing-only argument that was worth keeping: a write tool nobody adds to a list must not slip
# the gate. Listing readers rather than writers keeps that direction while letting a reader read.
test_an_unknown_tool_with_a_crate_path_still_denies if {
	denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "SomeNewWriteTool",
		"tool_input": {"file_path": "crates/er-quickload/src/lib.rs"},
		"signals": {"frida_evidence": ""},
	})
}

test_allow_post_tool_use_event if {
	not denied({
		"hook_event_name": "PostToolUse",
		"tool_name": "Edit",
		"tool_input": {"file_path": "crates/er-quickload/src/lib.rs"},
		"signals": {"frida_evidence": ""},
	})
}

test_allow_when_there_is_no_file_path_at_all if {
	not denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Edit",
		"tool_input": {},
		"signals": {"frida_evidence": ""},
	})
}

# No `tool_input` key at all. Nothing to gate, so nothing is denied -- but the rule must reach
# that answer rather than going undefined on the way, which is the failure mode that let the
# missing `signals` object through.
test_allow_when_there_is_no_tool_input_at_all if {
	not denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Edit",
		"signals": {"frida_evidence": ""},
	})
}

# The two defaults together: neither key present. Still an answer, still not a crash.
test_a_bare_event_is_answered_rather_than_undefined if {
	not denied({"hook_event_name": "PreToolUse", "tool_name": "Edit"})
}

# --- the denial says what to do about it --------------------------------------

# A refusal that does not name the instrument is a refusal the next agent argues
# with. The reason carries the two commands that produce the evidence, the path
# it refused, and what the reader actually said.
test_reason_names_the_frida_commands_and_the_target if {
	some decision in guard.deny with input as edit_event("crates/er-quickload/src/lib.rs", UNPROVEN_SILENT)
	decision.rule_id == RULE
	contains(decision.reason, "scripts/er-frida-up.py")
	contains(decision.reason, "scripts/er-frida-watch.py")
	contains(decision.reason, "crates/er-quickload/src/lib.rs")
	contains(decision.reason, "reported 0 messages")
}

test_reason_says_the_signal_was_absent_when_it_was if {
	some decision in guard.deny with input as {
		"hook_event_name": "PreToolUse",
		"tool_name": "Edit",
		"tool_input": {"file_path": "crates/er-quickload/src/lib.rs"},
		"signals": {},
	}
	decision.rule_id == RULE
	contains(decision.reason, "<signal absent>")
}

test_denial_is_high_severity if {
	some decision in guard.deny with input as edit_event("crates/er-quickload/src/lib.rs", "")
	decision.rule_id == RULE
	decision.severity == "HIGH"
}

# A reader is not an editor, and the routing metadata is not the contract.
#
# Added 2026-09-16 after a subagent reading `crates/er-telemetry-core/src/counters.rs` with the
# Read tool was refused by this policy and could not find anything in `.cupcake/` that explained
# it. The deny body checked only the path, so anything the engine routed here was refused. A gate
# whose purpose is to make an agent go and look must never be the thing that stops it looking.
test_allow_read_of_a_crate_file_with_no_evidence if {
	event := {
		"hook_event_name": "PreToolUse",
		"tool_name": "Read",
		"tool_input": {"file_path": "crates/er-telemetry-core/src/counters.rs"},
		"signals": {"frida_evidence": UNPROVEN_NOTHING},
	}

	count(guard.deny) == 0 with input as event
}

test_allow_grep_of_a_crate_file_with_no_evidence if {
	event := {
		"hook_event_name": "PreToolUse",
		"tool_name": "Grep",
		"tool_input": {"file_path": "crates/er-invasion-warp/src/lib.rs"},
		"signals": {"frida_evidence": UNPROVEN_NOTHING},
	}

	count(guard.deny) == 0 with input as event
}

# An absent `tool_name` denies, for the same reason an unknown one does: the empty string is not
# a reader, and a gate that opens on a missing key opens on exactly the malformed input it is
# least able to judge.
test_deny_when_the_tool_name_key_is_absent if {
	event := {
		"hook_event_name": "PreToolUse",
		"tool_input": {"file_path": "crates/er-invasion-warp/src/lib.rs"},
		"signals": {"frida_evidence": UNPROVEN_NOTHING},
	}

	count(guard.deny) == 1 with input as event
}

# --- the second instrument: in-process telemetry, scoped to one crate ----------
#
# Frida reaches the game. It does not reach our own DLLs: a release `cdylib` here exports
# `DllMain` and nothing else, so an unexported static or a `pub(crate)` seam has no address to
# attach to. For that class the evidence is a line our own code printed at the branch during a
# live run, quoted verbatim -- and because a measurement of one shell's branch says nothing about
# any other crate, it opens only the crate whose log it came out of.
#
# Copied from `scripts/er-frida-evidence.py`'s own format string, and asserted there by the
# selftest, so a change to the wording breaks both halves rather than silently widening this.
PROVEN_TELEMETRY := "PROVEN telemetry crate=er-save-game-row log=/games/er-save-game-row.log line='05_010 stats-panel edit not armed -- no browse row'"

test_allow_the_crate_the_telemetry_came_from if {
	count(guard.deny) == 0 with input as edit_event(
		"crates/er-save-game-row/src/lib.rs",
		PROVEN_TELEMETRY,
	)
}

test_allow_that_crate_by_absolute_path_too if {
	count(guard.deny) == 0 with input as edit_event(
		"/home/u/er-mods-rs/crates/er-save-game-row/src/lib.rs",
		PROVEN_TELEMETRY,
	)
}

# The scope is the whole point. One shell's branch is not evidence about another crate, and a
# telemetry verdict that opened the tree would be strictly weaker than the Frida path it sits
# beside rather than narrower.
test_deny_a_different_crate_on_the_same_telemetry if {
	denied(edit_event("crates/er-quickload/src/lib.rs", PROVEN_TELEMETRY))
}

# A neighbouring crate whose name merely starts with the licensed one. The trailing slash in the
# path fragment is what keeps `er-save-game-row` from opening `er-save-game-row-core`.
test_deny_a_crate_whose_name_extends_the_licensed_one if {
	denied(edit_event("crates/er-save-game-row-core/src/lib.rs", PROVEN_TELEMETRY))
}

# A telemetry verdict that has lost its `crate=` field has no scope, so it opens nothing. It must
# not fall through to the unscoped Frida rule, which would turn a formatting slip in the reader
# into permission to edit the whole tree.
test_deny_a_telemetry_verdict_with_no_crate_field if {
	denied(edit_event("crates/er-save-game-row/src/lib.rs", "PROVEN telemetry log=/games/x.log"))
}

test_deny_a_telemetry_verdict_whose_crate_field_is_empty if {
	denied(edit_event("crates/er-save-game-row/src/lib.rs", "PROVEN telemetry crate= log=/x.log"))
}

# Free text from a log sits to the right of the crate name, so it must not be able to impersonate
# the field that decides scope. The match is anchored at the front of the verdict for this case.
test_deny_when_a_second_crate_field_appears_in_the_quoted_line if {
	denied(edit_event(
		"crates/er-quickload/src/lib.rs",
		"PROVEN telemetry crate=er-save-game-row log=/x.log line='... crate=er-quickload ...'",
	))
}

# And the Frida path is unchanged: its verdict still opens every crate, because the instrument
# reaches the game and every crate here eventually talks to the game.
test_a_frida_verdict_still_opens_any_crate if {
	count(guard.deny) == 0 with input as edit_event("crates/er-save-game-row/src/lib.rs", PROVEN)
	count(guard.deny) == 0 with input as edit_event("crates/er-quickload/src/lib.rs", PROVEN)
}

# --- the same edit, typed into the Bash tool (bd er-effects-rs-wuij) ----------
#
# The rule routed on the write TOOLS and denied on `tool_input.file_path`; a Bash call has
# no such key, so the whole rule was undefined for it and every shell spelling of the edit
# went through. The first case below is the exact loop that did it.

bash_event(cmd, signal) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd},
	"signals": {"frida_evidence": signal},
}

test_deny_the_sed_loop_that_rewrote_four_crate_sources if {
	denied(bash_event(
		"for c in er-refill-all er-enemynpc-effects er-npc-possess er-invasion-path; do sed -i 's/^const DEFAULT_CONFIG_TOML/pub(crate) const DEFAULT_CONFIG_TOML/' crates/$c/src/config.rs; done",
		UNPROVEN_NOTHING,
	))
}

test_deny_heredoc_redirect_into_a_crate_source if {
	denied(bash_event("cat > crates/er-title-flow/src/lib.rs <<'EOF'\npub fn x() {}\nEOF", UNPROVEN_NOTHING))
}

test_deny_append_redirect_with_no_space if {
	denied(bash_event("echo 'pub fn x() {}' >>crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_tee_into_a_crate_source if {
	denied(bash_event("echo 'pub fn x() {}' | tee crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_git_checkout_of_a_crate_source if {
	denied(bash_event("git checkout -- crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_git_restore_of_a_crate_source if {
	denied(bash_event("git restore crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_git_restore_with_a_source_tree if {
	denied(bash_event("git restore --source HEAD~1 crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_git_restore_worktree if {
	denied(bash_event("git restore --worktree crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_git_restore_staged_plus_worktree if {
	denied(bash_event("git restore --staged --worktree crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_git_restore_short_cluster_with_worktree if {
	denied(bash_event("git restore -SW crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_git_restore_short_worktree_beside_staged if {
	denied(bash_event("git restore -S -W -- crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

# A `--staged` that is a pathspec after `--` is not the flag.
test_deny_git_restore_where_staged_is_only_a_pathspec if {
	denied(bash_event("git restore -- --staged crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_git_restore_unstage_chained_to_a_worktree_restore if {
	denied(bash_event(
		"git restore --staged crates/er-title-flow/src/lib.rs && git restore crates/er-title-flow/src/lib.rs",
		UNPROVEN_NOTHING,
	))
}

test_deny_copy_onto_a_crate_source if {
	denied(bash_event("cp -f /tmp/patched.rs crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_removal_of_a_crate_source if {
	denied(bash_event("rm -f crates/er-title-flow/src/constants_moved.rs", UNPROVEN_NOTHING))
}

# --- and every READ of the same file stays free ------------------------------
#
# A gate whose purpose is to make an agent go and look must never be the thing that stops
# it looking. This is the Bash half of the reasoning that put `Read` in `read_only_tools`.

test_allow_sed_range_read if {
	not denied(bash_event("sed -n '1,40p' crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_allow_cat_of_a_crate_source if {
	not denied(bash_event("cat crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_allow_git_diff_of_a_crate_source if {
	not denied(bash_event("git diff crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

# Unstaging touches the index only. Refused 2026-10-02 as if it rewrote the sources.
test_allow_git_restore_staged if {
	not denied(bash_event(
		"git restore --staged crates/er-title-flow/src/lib.rs crates/er-title-flow/src/config.rs",
		UNPROVEN_NOTHING,
	))
}

test_allow_git_restore_staged_short_flag if {
	not denied(bash_event("git restore -S -- crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_allow_git_restore_staged_with_a_source_tree if {
	not denied(bash_event("git restore --staged --source=HEAD crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_allow_git_reset_of_paths if {
	not denied(bash_event("git reset -- crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_allow_git_reset_head_of_paths if {
	not denied(bash_event("git reset HEAD crates/er-title-flow/src/lib.rs", UNPROVEN_NOTHING))
}

test_allow_copying_a_crate_source_somewhere_else if {
	not denied(bash_event("cp crates/er-title-flow/src/lib.rs /tmp/before.rs", UNPROVEN_NOTHING))
}

test_allow_a_command_that_merely_names_a_crate_source if {
	not denied(bash_event(
		`git commit -m "fix(title-flow): sed -i rewrote crates/er-title-flow/src/lib.rs before this"`,
		UNPROVEN_NOTHING,
	))
}

test_allow_writing_a_non_rust_file_under_crates if {
	not denied(bash_event("cat > crates/er-title-flow/Cargo.toml <<'EOF'\n[package]\nEOF", UNPROVEN_NOTHING))
}

test_allow_writing_a_rust_file_outside_crates if {
	not denied(bash_event("sed -i 's/a/b/' build-support/prologue_build.rs", UNPROVEN_NOTHING))
}

# --- and a measurement opens the Bash spelling too, with the same scope -------

test_allow_the_sed_loop_with_a_frida_verdict if {
	not denied(bash_event("sed -i 's/a/b/' crates/er-title-flow/src/lib.rs", PROVEN))
}

test_deny_the_sed_loop_when_telemetry_licensed_a_different_crate if {
	denied(bash_event("sed -i 's/a/b/' crates/er-title-flow/src/lib.rs", PROVEN_TELEMETRY))
}

# --- a destination the shell computes (2026-10-15) -----------------------------
#
# A subagent wrote two crate sources with `sed ... > $W/lib.rs`, `W` holding
# `.worktrees/er-render-scale/crates/er-overlay-theme/src`. The rule only read a literal
# `crates/` in the written word, so it never saw either write.

THEME_SRC := "/home/banon/projects/er-mods-rs/.worktrees/er-render-scale/crates/er-overlay-theme/src"

bash_event_in(cmd, signal, cwd) := object.union(bash_event(cmd, signal), {"cwd": cwd})

reason_of(event) := reason if {
	some decision in guard.deny with input as event
	decision.rule_id == RULE
	reason := decision.reason
}

# The measured shape: the variable assigned from a literal in the same command, so the
# guard can follow it to the crate.
test_deny_the_sed_into_a_variable_directory_that_was_measured if {
	denied(bash_event(
		concat("", ["W=", THEME_SRC, "; sed 's/old/new/' $W/lib.rs.orig > $W/lib.rs"]),
		UNPROVEN_NOTHING,
	))
}

test_the_followed_variable_is_named_as_the_target if {
	reason := reason_of(bash_event(
		concat("", ["W=", THEME_SRC, "; sed 's/old/new/' /tmp/lib.rs > $W/lib.rs"]),
		UNPROVEN_NOTHING,
	))
	contains(reason, "crates/er-overlay-theme/src/lib.rs")
}

test_deny_a_relative_or_quoted_assignment_followed_the_same_way if {
	denied(bash_event(
		"W=.worktrees/er-render-scale/crates/er-overlay-theme/src; sed 's/a/b/' /tmp/x > $W/lib.rs",
		UNPROVEN_NOTHING,
	))
	denied(bash_event(
		`W=".worktrees/er-render-scale/crates/er-overlay-theme/src"; sed 's/a/b/' /tmp/x > "${W}/lib.rs"`,
		UNPROVEN_NOTHING,
	))
}

# A variable the command never assigns: the guard cannot see where it lands, the command
# names a Rust file, so it refuses.
test_deny_a_redirect_into_an_unassigned_variable if {
	denied(bash_event("sed 's/a/b/' /tmp/x > $W/lib.rs", UNPROVEN_NOTHING))
	denied(bash_event("echo 'pub fn x() {}' >>$W/lib.rs", UNPROVEN_NOTHING))
	denied(bash_event("sed 's/a/b/' /tmp/x 1>$W/lib.rs", UNPROVEN_NOTHING))
}

test_the_unresolved_refusal_says_what_it_could_not_follow if {
	reason := reason_of(bash_event("sed 's/a/b/' /tmp/x > $W/lib.rs", UNPROVEN_NOTHING))
	contains(reason, "Unresolved destination: $W/lib.rs")
	contains(reason, "Spell the destination as a literal path")
}

test_deny_an_assignment_from_a_substitution if {
	denied(bash_event(
		`W=$(git rev-parse --show-toplevel)/crates/er-overlay-theme/src; sed 's/a/b/' /tmp/x > "$W/lib.rs"`,
		UNPROVEN_NOTHING,
	))
}

test_deny_every_writer_aimed_through_a_variable if {
	denied(bash_event(`sed -i 's/a/b/' "${W}/lib.rs"`, UNPROVEN_NOTHING))
	denied(bash_event("cat /tmp/x | tee $W/lib.rs >/dev/null", UNPROVEN_NOTHING))
	denied(bash_event("cp -f /tmp/x.rs $W/lib.rs", UNPROVEN_NOTHING))
	denied(bash_event(`mv /tmp/x.rs "$W/lib.rs"`, UNPROVEN_NOTHING))
	denied(bash_event("install -m644 /tmp/x.rs $W/lib.rs", UNPROVEN_NOTHING))
	denied(bash_event("perl -pi -e 's/a/b/' $W/lib.rs", UNPROVEN_NOTHING))
	denied(bash_event("git checkout -- $W/lib.rs", UNPROVEN_NOTHING))
}

# The whole path in the variable: no `.rs` in the written word, so the command naming
# `crates` elsewhere is what makes it a refusal.
test_deny_a_variable_holding_the_whole_path if {
	denied(bash_event("F=$(ls crates/er-x/src/*.rs | head -1); sed -i 's/a/b/' $F", UNPROVEN_NOTHING))
	denied(bash_event("for f in crates/er-x/src/lib.rs crates/er-x/src/config.rs; do sed -i 's/a/b/' $f; done", UNPROVEN_NOTHING))
}

test_deny_a_backtick_destination if {
	denied(bash_event("sed -i 's/a/b/' `git ls-files crates/er-overlay-theme | head -1`", UNPROVEN_NOTHING))
}

# The same writes inside a command that carries a substitution, which the shared
# decomposition reads differently.
test_deny_an_indirect_write_beside_a_substitution if {
	denied(bash_event("x=$(date); sed -i 's/a/b/' $W/lib.rs", UNPROVEN_NOTHING))
}

# A directory the command moves into first.
test_deny_a_relative_write_after_a_cd_into_a_crate if {
	denied(bash_event("cd crates/er-overlay-theme/src && sed -i 's/a/b/' lib.rs", UNPROVEN_NOTHING))
	denied(bash_event(concat("", ["cd ", THEME_SRC, " && cat > lib.rs <<'EOF'\npub fn x() {}\nEOF"]), UNPROVEN_NOTHING))
	denied(bash_event("(cd crates/er-overlay-theme/src && sed -i 's/a/b/' lib.rs)", UNPROVEN_NOTHING))
	denied(bash_event("pushd crates/er-overlay-theme && sed -i 's/a/b/' src/lib.rs", UNPROVEN_NOTHING))
}

test_deny_a_relative_write_after_a_cd_the_guard_cannot_follow if {
	denied(bash_event("cd $W && sed -i 's/a/b/' lib.rs", UNPROVEN_NOTHING))
	denied(bash_event("cd - && sed -i 's/a/b/' lib.rs", UNPROVEN_NOTHING))
}

# The session's working directory persists between Bash calls, so a `cd` in an earlier call
# puts every relative path inside the crate.
test_deny_a_relative_write_from_a_working_directory_inside_a_crate if {
	denied(bash_event_in("sed -i 's/a/b/' lib.rs", UNPROVEN_NOTHING, THEME_SRC))
}

test_deny_git_dash_c_spellings if {
	denied(bash_event("git -C crates/er-overlay-theme checkout -- src/lib.rs", UNPROVEN_NOTHING))
	denied(bash_event(
		"git -C /home/banon/projects/er-mods-rs checkout -- crates/er-overlay-theme/src/lib.rs",
		UNPROVEN_NOTHING,
	))
}

test_deny_a_copy_into_a_crate_directory if {
	denied(bash_event("cp /tmp/lib.rs crates/er-overlay-theme/src/", UNPROVEN_NOTHING))
	denied(bash_event("cp -t crates/er-overlay-theme/src /tmp/lib.rs", UNPROVEN_NOTHING))
	denied(bash_event("install -m 644 /tmp/lib.rs crates/er-overlay-theme/src", UNPROVEN_NOTHING))
}

# A move removes its source.
test_deny_moving_a_crate_source_away if {
	denied(bash_event("mv crates/er-overlay-theme/src/lib.rs /tmp/", UNPROVEN_NOTHING))
}

test_deny_paths_fed_through_xargs_or_find if {
	denied(bash_event("find crates -name '*.rs' | xargs sed -i 's/a/b/'", UNPROVEN_NOTHING))
	denied(bash_event("git ls-files crates | xargs -I {} sed -i 's/a/b/' {}", UNPROVEN_NOTHING))
	denied(bash_event("find crates -name '*.rs' -exec sed -i 's/a/b/' {} +", UNPROVEN_NOTHING))
	denied(bash_event("find crates -name '*.rs.orig' -delete", UNPROVEN_NOTHING))
}

# Two literal spellings that slipped before for reasons of their own: a closing quote on the
# path, and the `-pi` cluster perl is usually written with.
test_deny_a_quoted_literal_crate_path if {
	denied(bash_event(`sed -i 's/a/b/' "crates/er-x/src/lib.rs"`, UNPROVEN_NOTHING))
}

test_deny_perl_pi if {
	denied(bash_event("perl -pi -e 's/a/b/' crates/er-x/src/lib.rs", UNPROVEN_NOTHING))
	denied(bash_event("perl -i.bak -pe 's/a/b/' crates/er-x/src/lib.rs", UNPROVEN_NOTHING))
}

# Telemetry licenses one crate; a destination nobody can locate is not in it.
test_deny_an_unresolved_write_on_a_telemetry_verdict if {
	denied(bash_event("sed 's/a/b/' /tmp/x > $W/lib.rs", PROVEN_TELEMETRY))
}

test_allow_an_unresolved_write_on_a_frida_verdict if {
	not denied(bash_event("sed 's/a/b/' /tmp/x > $W/lib.rs", PROVEN))
}

# --- and the computed destinations that must stay allowed -----------------------

test_allow_cargo_output_to_a_scratchpad_log if {
	not denied(bash_event(
		"cargo build -p er-quickload > /tmp/claude-1000/-home-banon-projects-er-mods-rs/x/scratchpad/build.log 2>&1",
		UNPROVEN_NOTHING,
	))
	not denied(bash_event("cargo build -p er-quickload > $S/build.log 2>&1", UNPROVEN_NOTHING))
	not denied(bash_event(
		"S=/tmp/claude-1000/x/scratchpad; cargo check --manifest-path crates/er-quickload/Cargo.toml > $S/check.log 2>&1; echo exit=$?",
		UNPROVEN_NOTHING,
	))
	not denied(bash_event(
		`cargo test --manifest-path crates/er-x/Cargo.toml 2>&1 | tee "$SCRATCH/test.log"`,
		UNPROVEN_NOTHING,
	))
}

# An extensionless log is followed to its literal assignment.
test_allow_a_variable_assigned_a_literal_outside_crates if {
	not denied(bash_event(
		`LOG=/tmp/x/check; cargo test --manifest-path crates/er-x/Cargo.toml > "$LOG" 2>&1`,
		UNPROVEN_NOTHING,
	))
	not denied(bash_event("S=/tmp/scratch; cp crates/er-x/src/lib.rs $S/before.rs", UNPROVEN_NOTHING))
	not denied(bash_event("cat crates/er-x/src/lib.rs > $HOME/notes.rs", UNPROVEN_NOTHING))
}

# A computed destination in a command that names no crate and no Rust file.
test_allow_an_unresolved_log_in_a_command_naming_no_rust if {
	not denied(bash_event(`cargo test -p er-x 2>&1 | tee "$LOG"`, UNPROVEN_NOTHING))
	not denied(bash_event("find target -name '*.d' -delete", UNPROVEN_NOTHING))
}

# A `$` in an editor's script is a regex anchor or a value, not a destination.
test_allow_an_editor_script_with_a_dollar if {
	not denied(bash_event(`sed -i "s/$OLD/$NEW/" scripts/x.py; ls crates/`, UNPROVEN_NOTHING))
	not denied(bash_event("sed -i 's/foo$/bar/' scripts/x.py; ls crates/", UNPROVEN_NOTHING))
	not denied(bash_event(`sed -n "s/$A/x/p" crates/er-x/src/lib.rs`, UNPROVEN_NOTHING))
}

# Prose quoting the very shape this refuses.
test_allow_a_commit_message_quoting_the_shape if {
	not denied(bash_event(
		`git commit -m "fix(cupcake): the subagent ran sed ... > $W/lib.rs and tee $W/lib.rs into crates/"`,
		UNPROVEN_NOTHING,
	))
	not denied(bash_event(
		`bd remember --key k 'a write such as cp /tmp/x.rs $W/lib.rs slipped past the crates/ gate'`,
		UNPROVEN_NOTHING,
	))
}

test_allow_reads_that_carry_indirection if {
	not denied(bash_event("for f in $(git ls-files 'crates/**/*.rs'); do wc -l $f; done > $S/counts.txt", UNPROVEN_NOTHING))
	not denied(bash_event(`F=$(git diff --name-only | grep -E '\.(go|rs)$'); echo $F`, UNPROVEN_NOTHING))
	not denied(bash_event("x=$(date); cp crates/er-x/src/lib.rs /tmp/y.rs", UNPROVEN_NOTHING))
	not denied(bash_event("git -C /home/banon/projects/er-mods-rs diff -- crates/er-x/src/lib.rs", UNPROVEN_NOTHING))
	not denied(bash_event("perl -MList::Util=min -ne 'print' crates/er-x/src/lib.rs", UNPROVEN_NOTHING))
	not denied(bash_event("cp crates/er-x/src/lib.rs /tmp/backup/", UNPROVEN_NOTHING))
}

test_allow_a_relative_write_after_a_cd_outside_crates if {
	not denied(bash_event("cd /tmp/x && cat > a.rs <<'EOF'\nfn main() {}\nEOF", UNPROVEN_NOTHING))
	not denied(bash_event("cd crates/er-x && cargo build > build.log 2>&1", UNPROVEN_NOTHING))
	not denied(bash_event_in("sed -i 's/a/b/' build-support/x.rs", UNPROVEN_NOTHING, "/home/banon/projects/er-mods-rs"))
}

# --- resolving a conflict of a stopped rebase/merge ---------------------------
#
# Measured 2026-10-07: a `git rebase --onto` stopped on a conflict in
# `crates/er-npc-summons/src/addr.rs`, and the Edit deleting the markers was refused
# with `UNPROVEN spent-by-commit`. The reader now answers `CONFLICT-RESOLUTION` when
# git says the file is unmerged in a stopped operation; the policy admits it only for
# the path the verdict names and only for the single-path write tools.

CONFLICTED := "/home/banon/projects/er-mods-rs/.worktrees/ledge-reveal-check/crates/er-npc-summons/src/addr.rs"

conflict_verdict(op, path) := concat("", ["CONFLICT-RESOLUTION op=", op, " path=", path])

test_allow_edit_resolving_a_rebase_conflict if {
	not denied(edit_event(CONFLICTED, conflict_verdict("rebase", CONFLICTED)))
}

test_allow_write_resolving_a_merge_conflict if {
	not denied({
		"hook_event_name": "PreToolUse",
		"tool_name": "Write",
		"tool_input": {"file_path": "crates/er-npc-summons/src/addr.rs"},
		"signals": {"frida_evidence": conflict_verdict("merge", "crates/er-npc-summons/src/addr.rs")},
	})
}

test_allow_conflict_verdict_with_a_trailing_newline if {
	not denied(edit_event(CONFLICTED, concat("", [conflict_verdict("cherry-pick", CONFLICTED), "\n"])))
}

# An ordinary edit with no conflict in progress gets the ordinary verdict and is refused.
test_deny_ordinary_edit_with_no_conflict_in_progress if {
	denied(edit_event(CONFLICTED, UNPROVEN_SPENT))
}

# A verdict about one conflicted file must not open another.
test_deny_conflict_verdict_naming_a_different_file if {
	denied(edit_event(
		"/home/banon/projects/er-mods-rs/.worktrees/ledge-reveal-check/crates/er-npc-summons/src/lib.rs",
		conflict_verdict("rebase", CONFLICTED),
	))
}

test_deny_conflict_verdict_whose_path_is_only_a_prefix if {
	denied(edit_event(CONFLICTED, conflict_verdict("rebase", "/home/banon/projects/er-mods-rs/.worktrees/ledge-reveal-check/crates/er-npc-summons/src/addr")))
}

test_deny_conflict_verdict_with_an_unknown_operation if {
	denied(edit_event(CONFLICTED, conflict_verdict("stash", CONFLICTED)))
}

test_deny_conflict_verdict_carrying_a_second_line if {
	denied(edit_event(CONFLICTED, concat("", [conflict_verdict("rebase", CONFLICTED), "\nPROVEN"])))
}

# The Bash spelling is never admitted by a conflict verdict.
test_deny_bash_write_with_a_conflict_verdict if {
	denied(bash_event(
		"sed -i '/^<<<<<<<\\|^=======\\|^>>>>>>>/d' crates/er-npc-summons/src/addr.rs",
		conflict_verdict("rebase", "crates/er-npc-summons/src/addr.rs"),
	))
}

test_deny_conflict_verdict_from_a_failure_record if {
	denied(edit_event(CONFLICTED, {"error": "x", "exit_code": 1, "output": conflict_verdict("rebase", CONFLICTED), "success": false}))
}

# --- the user's waiver (2026-10-22) ------------------------------------------------------------
#
# Copied from `scripts/er-frida-evidence.py`'s format string, which its selftest asserts.
PROVEN_WAIVER := "PROVEN waiver crate=er-quickload reason='pure move: dissolve src/experiments/ into src/'"

test_allow_the_waived_crate if {
	count(guard.deny) == 0 with input as edit_event("crates/er-quickload/src/lib.rs", PROVEN_WAIVER)
}

test_allow_the_waived_crate_in_a_worktree if {
	count(guard.deny) == 0 with input as edit_event(
		"/home/u/er-mods-rs/.worktrees/x/crates/er-quickload/src/lifecycle.rs",
		PROVEN_WAIVER,
	)
}

# A waiver is no wider than telemetry: it must not fall through to the unscoped Frida rule.
test_deny_another_crate_on_a_waiver if {
	denied(edit_event("crates/er-title-flow/src/lib.rs", PROVEN_WAIVER))
}

test_deny_a_waiver_verdict_with_no_crate_field if {
	denied(edit_event("crates/er-quickload/src/lib.rs", "PROVEN waiver reason='x'"))
}

test_deny_the_agent_issuing_a_waiver if {
	denied(bash_event(
		"python3 scripts/er-frida-evidence.py --waive --crate er-quickload --reason 'pure move, no behaviour change'",
		PROVEN,
	))
}

test_allow_the_agent_reading_the_verdict if {
	count(guard.deny) == 0 with input as bash_event("python3 scripts/er-frida-evidence.py --check", UNPROVEN_NOTHING)
}
