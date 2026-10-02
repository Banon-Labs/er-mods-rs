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

# --- 2026-09-26: a build word that is only an argument or a pattern ----------------------------
#
# The exact command denied on the native-Linux dev box: a read-only check for running cargo/rustc
# builds and agent sessions before a disk cleanup. `cargo|rustc` is pgrep's pattern and the grep
# filters pgrep's process list; nothing was built.
test_the_2026_09_26_process_listing_is_allowed if {
	not denied(concat("", [
		`pgrep -a -f 'cargo|rustc' | grep -v pgrep | cut -c1-200 | head; `,
		`echo "--- agents:"; pgrep -a -x claude | cut -c1-120; `,
		`pgrep -a -f '(^| |/)pi( |$)' | cut -c1-120 | head; `,
		`echo "--- scan:"; cat /tmp/.../tasks/bv7bzk2t8.output`,
	]))
}

test_build_word_as_an_argument_is_allowed if {
	not denied(`pgrep -a -f 'cargo|rustc' | grep -v pgrep`)
	not denied("pgrep -af cargo | grep -v pgrep")
	not denied(`ps -eo pid,args | grep -E 'cargo|rustc'`)
	not denied("ps aux | grep cargo")
	not denied("ls ~/.cargo/bin | grep cargo")
	not denied("echo cargo build | grep build")
	not denied(`git log --oneline | grep 'cargo check'`)
	not denied("sudo -u cargo ls | grep x")
}

# Informational cargo is not a verdict: nothing compiles, so there is no exit code being guessed at.
test_informational_cargo_is_allowed if {
	not denied("cargo tree -i serde | grep serde")
	not denied("cargo metadata --format-version 1 | grep workspace_root")
	not denied("cargo --version | grep 1.8")
}

# The grep reads a DIFFERENT statement's output, not the build's.
test_grep_in_a_later_statement_is_allowed if {
	not denied("cargo build -p er-quickload; ls target/debug | grep dll")
	not denied("cargo check -p er-quickload && git status --short | grep crates")
}

# --- 2026-09-29: "build" the noun, in a script name ----------------------------------------------
#
# The exact command denied: a character-build catalog report grepped for a column value. The
# script's name held "builds", and the substring match read it as a repo build wrapper.
test_the_2026_09_29_builds_report_loop_is_allowed if {
	not denied(concat("", [
		`for slot in r1_1 r2_1; do for s in damage per-frame poise startup roll; do `,
		`timeout 14 python3 scripts/er-builds-pvp.py --rl 150 --slot $slot --sort $s --top 400 > $d/g.txt 2>&1; `,
		`n=$(awk 'NR>3' $d/g.txt | wc -l); awk '{print $1}' $d/g.txt; done; done 2>&1 | grep " 2H "`,
	]))
}

test_build_noun_in_a_script_name_is_allowed if {
	not denied(`python3 scripts/er-builds-pvp.py --rl 150 | grep " 2H "`)
	not denied("python3 scripts/decode-build-link.py url | grep Sword")
	not denied("python3 scripts/find-build-item.py dagger | grep id")
	not denied("python3 scripts/compare-build-inventory-order.py a b | grep diff")
}

# The verb-named wrappers are still builds.
test_verb_named_repo_scripts_are_denied if {
	denied("bash scripts/check.sh 2>&1 | grep FAIL")
	denied("bash scripts/act-check.sh --stage lint 2>&1 | grep -i error")
	denied("bash scripts/check-rust-build.sh | grep error")
	denied("python3 scripts/test-cupcake-policies.py | grep FAIL")
	denied("for s in lint policy; do bash scripts/check.sh --stage $s; done 2>&1 | grep FAIL")
}

# --- ...and every real build piped into a matcher still denies ---------------------------------

test_build_after_cd_is_denied if {
	denied("cd crates/er-quickload && cargo build 2>&1 | grep error")
}

test_build_in_a_shell_wrapper_payload_is_denied if {
	denied(`bash -c 'cargo check -p er-quickload 2>&1 | grep error'`)
}

test_build_by_absolute_path_is_denied if {
	denied("/home/banon/.cargo/bin/cargo build -p er-quickload 2>&1 | grep error")
}

test_build_behind_env_assignment_is_denied if {
	denied("env RUSTFLAGS=-Dwarnings cargo clippy -p er-quickload 2>&1 | grep warning")
	denied("RUSTFLAGS=-Dwarnings cargo clippy -p er-quickload 2>&1 | grep warning")
}

test_build_behind_shell_keywords_is_denied if {
	denied(`if ! cargo check -p er-quickload 2>&1 | grep -q '^error'; then echo clean; fi`)
	denied("time cargo build -p er-quickload 2>&1 | grep error")
	denied("npx tsc --noEmit | grep error")
}

test_build_in_command_substitution_is_denied if {
	denied("out=$(cargo build -p er-quickload 2>&1 | grep -c error)")
}

test_build_in_a_compound_command_is_denied if {
	denied("(cargo build -p er-quickload; echo done) 2>&1 | grep error")
	denied("{ cargo build -p er-quickload; echo done; } 2>&1 | grep error")
	denied("for c in er-quickload er-hook; do cargo test -p $c; done 2>&1 | grep FAILED")
}

test_xwin_build_through_tee_is_denied if {
	denied("cargo xwin build --release 2>&1 | tee build.log | grep -i error")
}

# cargo's built-in aliases are the same verdict.
test_cargo_alias_subcommand_is_denied if {
	denied("cargo c -p er-quickload 2>&1 | grep error")
	denied("cargo t -p er-game-base 2>&1 | rg FAILED")
}

# A read-only listing in one statement does not launder a real build-and-grep in the next.
test_listing_then_real_build_grep_is_denied if {
	denied("pgrep -af cargo | grep -v pgrep; cargo build -p er-quickload 2>&1 | grep error")
}
