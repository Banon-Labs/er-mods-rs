# OPA unit tests for no_grep_for_build_errors.
#
# The case that created the policy is `test_the_2026_09_03_command_is_denied`: that exact pipeline
# reported a FAILED cargo check as a clean build. Everything else here pins the boundary, because a
# guard that also blocks excerpting output or reading a log would just get worked around.
#
# Run with:
#   opa test .cupcake/system/commands.rego \
#     .cupcake/policies/claude/no_grep_for_build_errors.rego \
#     .cupcake/tests/no_grep_for_build_errors_test.rego
package cupcake.policies.claude.no_grep_for_build_errors_test

import rego.v1

import data.cupcake.policies.claude.no_grep_for_build_errors as guard

bash(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd},
}

denied(cmd) if {
	some d in guard.deny with input as bash(cmd)
	d.rule_id == "ER-EFFECTS-NO-GREP-FOR-BUILD-ERRORS"
}

# --- the incident ------------------------------------------------------------------------------

test_the_2026_09_03_command_is_denied if {
	denied("timeout 28 cargo check -p er-quickload 2>&1 | grep -E 'error' -A6 | head -20; echo \"--- clean ---\"")
}

# --- the same mistake with more plumbing -------------------------------------------------------

test_tee_before_the_matcher_is_still_denied if {
	denied("cargo build --release 2>&1 | tee build.log | grep error")
}

test_stderr_merge_shorthand_is_denied if {
	denied("cargo xwin build --release |& grep -c error")
}

test_ripgrep_counts_as_a_matcher if {
	denied("cargo test -p er-game-base 2>&1 | rg '^error'")
}

test_repo_build_script_is_covered if {
	denied("bash scripts/er-build-dlls.sh er-quickload 2>&1 | grep -i failed")
}

test_other_build_tools_are_covered if {
	denied("opa test .cupcake/policies | grep FAIL")
	denied("make -j8 2>&1 | egrep 'Error'")
	denied("pytest -q 2>&1 | grep -E 'failed|error'")
}

# --- the boundary: shaping output is not adjudicating it ---------------------------------------

test_tail_is_allowed if {
	not denied("timeout 28 cargo check -p er-quickload 2>&1 | tail -20")
}

test_head_and_sed_and_awk_are_allowed if {
	not denied("cargo build 2>&1 | head -40")
	not denied("cargo build 2>&1 | sed -n '1,20p'")
	not denied("cargo build 2>&1 | awk '{print $1}'")
}

# The exit code being consulted is the whole point, and must never be blocked.
test_checking_the_exit_code_is_allowed if {
	not denied("cargo check -p er-quickload; echo \"exit=$?\"")
	not denied("cargo build --release || tail -40 build.log")
}

# Grepping a log ALREADY on disk is reading evidence after the exit code was believed, not
# substituting for it.
test_grepping_a_log_file_is_allowed if {
	not denied("grep -n 'E0603' build4.log")
	not denied("rg 'could not compile' /tmp/build.log")
}

# No build verb at all -> nothing to adjudicate.
test_unrelated_grep_pipeline_is_allowed if {
	not denied("cat er-invasion-warp.log | grep heartbeat")
	not denied("ls crates | grep quickload")
}

# A verb that merely CONTAINS a build word is not a build verb.
test_lookalike_verbs_do_not_match if {
	not denied("mycargo status | grep error")
	not denied("./not-make.sh | grep error")
}

# --- 2026-10-02 false positives: a build word as an argument is not a build --------------------
#
# Both commands below were denied while running nothing but grep/sed/python over files. The
# trigger was not `Cargo.toml` (the verb match is case-sensitive) but the path
# `scripts/check-me3-dll-conflicts.py`: it matched the repo-build-script verb after a plain space
# in argument position, and again after the `'` of a python string literal. A build verb now only
# counts in command position.

test_read_only_grep_naming_check_script_and_cargo_toml_is_allowed if {
	not denied("cd /home/banon/projects/er-mods-rs && grep -P \"\\t0x998260$|\\t0x975890$\" docs/recon/rva-map-1162-to-1170.functions.tsv; sed -n 1922,1960p crates/er-hook/src/lib.rs | grep -n \"pub unsafe fn\"; ls scripts/me3-dll-list.py docs/ci-gate-portability.tsv >/dev/null && grep -n \"er-inventory-sort\" scripts/me3-dll-list.py scripts/check-me3-dll-conflicts.py Cargo.toml | head")
}

test_python_reading_check_script_and_cargo_toml_is_allowed if {
	not denied("cd /home/banon/projects/er-mods-rs && python3 -c \"import re\nfor f in ['docs/recon/rva-map-1162-to-1170.functions.tsv','scripts/me3-dll-list.py','scripts/check-me3-dll-conflicts.py','Cargo.toml']:\n  [print(f,l) for l in open(f) if 'er-inventory-sort' in l]\"; sed -n 1922,1990p crates/er-hook/src/lib.rs | grep \"pub unsafe fn\"")
}

test_build_word_as_grep_argument_is_allowed if {
	not denied("grep -n cargo Cargo.toml | grep -v '#'")
	not denied("cat scripts/check.sh | grep opa")
}

test_cargo_check_into_grep_is_still_denied if {
	denied("cargo check -p er-quickload 2>&1 | grep -E 'error'")
	denied("cd /home/banon/projects/er-mods-rs && cargo check -p er-quickload 2>&1 | grep -E 'error'")
	denied("RUSTFLAGS=-Dwarnings cargo build 2>&1 | grep error")
	denied("bash -c 'cargo build 2>&1 | grep error'")
	denied("(cargo build 2>&1) | grep error")
	denied("echo start; scripts/check-rust-build.sh | grep FAIL")
	denied("python3 scripts/check-me3-dll-conflicts.py | grep FAIL")
}
