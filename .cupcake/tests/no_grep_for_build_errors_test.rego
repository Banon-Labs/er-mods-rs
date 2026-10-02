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

# --- 2026-10-01: co-presence is not a pipeline ------------------------------------------------

# The command that was denied: the grep reads `ls` output, and the only script named is a PvP
# character-build ranking script piped into head.
test_the_2026_10_01_ls_filename_grep_is_allowed if {
	not denied("cd /home/banon/projects/er-mods-rs && ls scripts | grep -i -E 'er-builds|er-mechanics|ash' ; python3 scripts/er-builds-ash-choice.py --help 2>&1 | head -40")
}

test_grep_in_a_different_statement_from_the_build_is_allowed if {
	not denied("cargo check -p er-quickload; ls target | grep release")
	not denied("ls crates | grep quickload && cargo build")
}

test_grep_before_the_build_in_a_pipeline_is_allowed if {
	not denied("grep -l foo src/*.rs | xargs cargo fmt --")
}

test_builds_named_script_is_not_a_build_wrapper if {
	not denied("python3 scripts/er-builds-pvp.py --jobs 4 | grep Rivers")
}

test_build_word_in_a_grep_pattern_is_allowed if {
	not denied("ls scripts | grep -E 'build|check|test'")
	not denied("git log --oneline | grep 'cargo build'")
}

# The fix narrows co-presence to the pipeline; every shape of the real mistake still lands.
test_quoted_alternation_in_the_matcher_is_still_denied if {
	denied("cargo check 2>&1 | grep -E 'error|warning'")
}

test_bash_c_payload_is_still_denied if {
	denied("bash -c 'cargo build 2>&1 | grep error'")
}

test_delimited_wrapper_names_are_still_denied if {
	denied("bash scripts/check.sh --stage lint 2>&1 | grep FAIL")
	denied("bash scripts/check-rust-build.sh | rg error")
	denied("python3 scripts/test-cupcake-policies.py | grep -c FAIL")
	denied("bash scripts/act-check.sh --stage lint 2>&1 | grep -i error")
	denied("bash scripts/er-build-dlls.sh er-quickload 2>&1 | grep -i error")
}

test_build_after_an_unrelated_statement_is_still_denied if {
	denied("cd crates/er-quickload && cargo build 2>&1 | grep error")
	denied("ls; cargo test |& grep panicked")
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

# --- 2026-10-02 false positive: a grep in a sibling command does not read the build ------------
#
# The grep below reads a source file in its own `;`-separated command, before `cargo fmt` runs, and
# `cargo fmt` is piped nowhere. The guard used to match a build verb and a matcher anywhere in the
# whole line, so this was denied.

test_grep_in_earlier_sibling_command_is_allowed if {
	not denied("cd /home/banon/projects/er-mods-rs; sed -i 's/in this profile to see the X\"/in this profile to see the board\"/' crates/er-r3-view/src/imp.rs; grep -rn \"the X\\|an X\\| X \" crates/er-r3-view/src/imp.rs | grep -v \"X, Y\"; cargo fmt -p er-r3-view && python3 scripts/check-comment-caps.py crates/er-r3-view/src/board.rs")
}

test_grep_after_the_build_in_a_sibling_command_is_allowed if {
	not denied("cargo build 2>&1 | tee build.log; grep -n error build.log")
	not denied("cargo check && grep -n version Cargo.toml")
	not denied("cargo build & grep -rn TODO src")
}

test_build_continued_onto_a_new_line_is_still_denied if {
	denied("cargo build 2>&1 |\n  grep error")
	denied("cargo build 2>&1 \\\n  | grep error")
	denied("echo a && cargo build 2>&1 | tee log | grep error; echo done")
}

# --- 2026-10-02: a build-into-grep that is only quoted is prose ---------------------------------
#
# The hook driver that proved this policy through `scripts/cupcake-hook.sh` was denied while being
# written: `cat > cases.sh <<'EOF'` whose body quotes the deny cases. Quoted operands and data
# heredoc bodies are read through the shared decomposition, so their separators are not pipes.

test_quoted_build_into_grep_is_allowed if {
	not denied("git commit -m \"guard: deny cargo check 2>&1 | grep error\"")
	not denied("echo 'cargo build | grep error' >> notes.txt")
}

test_data_heredoc_quoting_build_into_grep_is_allowed if {
	not denied("cat > cases.sh <<'EOF'\nrun deny \"cargo check -p x 2>&1 | grep -E 'error'\"\nEOF\nbash cases.sh")
}

test_shell_read_heredoc_build_into_grep_is_still_denied if {
	denied("bash <<'EOF'\ncargo check -p x 2>&1 | grep error\nEOF")
}

# `scripts/cupcake-hook.sh` turns every unquoted newline into `; `, so a pipe continued onto the
# next line reaches the policy as `|;`. Measured through the hook on 2026-10-02: without this the
# trailing-pipe case above passed under the interpreter and was allowed in production.
test_pipe_continuation_as_the_hook_delivers_it_is_denied if {
	denied("cargo build 2>&1 |;   grep error")
	denied("cargo build 2>&1 | tee log |;   grep error")
}

test_or_after_the_build_is_still_not_a_pipe if {
	not denied("cargo build ||; grep -n error build.log")
}

# --- 2026-10-02: a builds-named script in one statement, a param grep in another -------------

# Denied by the co-presence form: `er-builds-embed.py` is a data-analysis script, and the only
# grep reads `er-param-read.py` output through `tr`, two statements later.
test_the_2026_10_02_param_grep_beside_a_builds_script_is_allowed if {
	not denied("cd /home/banon/projects/er-mods-rs && timeout 28 python3 scripts/er-builds-embed.py pairs \"Lance\" -n 12; timeout 28 python3 scripts/er-mechanics-reach.py Lance --grip one 2>&1 | sed -n 1,4p | cut -c1-60; python3 scripts/er-param-read.py SpEffectParam --row 6402 --names 2>/dev/null | tr ',' '\\n' | grep -iE \"name|blood|registance|Attack|'id'\" | head")
}

test_build_named_data_scripts_are_not_build_wrappers if {
	not denied("python3 scripts/er-build-import.py --dry-run | grep weapon")
	not denied("python3 scripts/er-builds-pvp.py --top 5 2>&1 | grep -i rivers")
	not denied("python3 scripts/er-builds-embed.py pairs Lance && ls scripts | grep er-builds")
}

test_cargo_build_into_grep_error_is_still_denied if {
	denied("cargo build 2>&1 | grep error")
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

test_grep_in_a_later_statement_is_allowed if {
	not denied("cargo build -p er-quickload; ls target/debug | grep dll")
	not denied("cargo check -p er-quickload && git status --short | grep crates")
}

# --- 2026-09-29: "build" the noun, in a script name ----------------------------------------------
#
# The exact command denied: a character-build catalog report grepped for a column value.
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

test_verb_named_repo_scripts_in_a_loop_are_denied if {
	denied("for s in lint policy; do bash scripts/check.sh --stage $s; done 2>&1 | grep FAIL")
}

# --- ...and every real build piped into a matcher still denies ---------------------------------

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
