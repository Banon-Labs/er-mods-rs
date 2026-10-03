# OPA unit tests for require_scoped_cargo.
#
# Not loaded by the cupcake engine (which scans .cupcake/policies/<harness>/
# and .cupcake/system/ only). Run with:
#   opa test .cupcake/policies/claude/require_scoped_cargo.rego \
#            .cupcake/tests/require_scoped_cargo_test.rego
# End-to-end engine coverage lives in scripts/test-cupcake-policies.py.
package cupcake.policies.claude.require_scoped_cargo_test

import rego.v1

import data.cupcake.policies.claude.require_scoped_cargo as guard

bash_event(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "timeout": 30000, "description": "test case"},
}

rule_ids(denials) := {d.rule_id | some d in denials}

denied_cargo(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	"ER-EFFECTS-REQUIRE-SCOPED-CARGO" in rule_ids(denials)
}

allowed(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	count(denials) == 0
}

# --- (a) unscoped cargo is DENIED --------------------------------------------

test_deny_bare_cargo_test if {
	denied_cargo("cargo test")
}

test_deny_bare_cargo_build if {
	denied_cargo("cargo build --release")
}

test_deny_bare_cargo_check if {
	denied_cargo("cargo check")
}

# The exact shape AGENTS.md documents for the DLL, which silently builds only
# default-members and reads as a successful incremental build.
test_deny_cargo_xwin_build_without_p if {
	denied_cargo("cargo xwin build --release --target x86_64-pc-windows-msvc")
}

# --workspace / --all are the explicit spelling of the thing being blocked.
test_deny_cargo_test_workspace if {
	denied_cargo("cargo test --workspace")
}

test_deny_cargo_test_all if {
	denied_cargo("cargo test --all")
}

# ...even paired with -p, which would otherwise satisfy has_package_flag.
test_deny_workspace_even_with_package if {
	denied_cargo("cargo test --workspace -p er-gfx")
}

# No escape hatch through quoting or a wrapper shell.
test_deny_cargo_inside_bash_c if {
	denied_cargo("bash -c 'cargo test'")
}

test_deny_cargo_after_separator if {
	denied_cargo("cd /tmp && cargo build")
}

test_deny_cargo_piped if {
	denied_cargo("cargo test 2>&1 | tail -5")
}

test_deny_path_prefixed_cargo if {
	denied_cargo("~/.cargo/bin/cargo test")
}

# Unquoted newlines arrive collapsed to spaces in the live engine; norm_command
# makes opa test see the same thing.
test_deny_cargo_on_second_line if {
	denied_cargo("echo hi\ncargo test")
}

# --- (b) scoped cargo is ALLOWED ---------------------------------------------

test_allow_cargo_test_with_p if {
	allowed("cargo test -p er-npc-possess")
}

test_allow_cargo_test_multiple_p if {
	allowed("cargo test -p er-quickload -p er-title-flow --lib")
}

test_allow_long_package_flag if {
	allowed("cargo test --package er-gfx")
}

test_allow_equals_package_flag if {
	allowed("cargo build --package=er-hook")
}

test_allow_cargo_xwin_build_with_p if {
	allowed("cargo xwin build --release --target x86_64-pc-windows-msvc -p er-invasion-warp")
}

test_allow_manifest_path_with_p if {
	allowed("cargo test --manifest-path /repo/Cargo.toml -p er-save-loader")
}

# --- (c) non-building cargo subcommands are ALLOWED --------------------------

# Whole-tree formatting is the point of `cargo fmt`, and it compiles nothing.
test_allow_cargo_fmt_all if {
	allowed("cargo fmt --all -- --check")
}

test_allow_cargo_metadata if {
	allowed("cargo metadata --no-deps --format-version 1")
}

test_allow_cargo_tree if {
	allowed("cargo tree -i syn")
}

test_allow_cargo_version if {
	allowed("cargo --version")
}

# A word merely CONTAINING cargo must not match.
test_allow_cargo_substring_word if {
	allowed("echo cargotest")
}

test_allow_cargo_culted_path_word if {
	allowed("ls /home/banon/.cargo/registry")
}

# --- (d) text-mention exemptions ---------------------------------------------

# bd records text; a single non-chained bd command may describe the guard.
test_allow_bd_remember_mentioning_cargo if {
	allowed("$HOME/.local/bin/bd remember --key k \"agents must run cargo test -p <crate>, never bash scripts/check.sh\"")
}

# A git commit message may describe the change that adds this guard.
test_allow_git_commit_message_mentioning_cargo if {
	allowed("git commit -m \"guard: deny unscoped cargo test and scripts/check.sh\"")
}

# ...but a chained batch is not a single text-recording invocation.
test_deny_bd_chained_with_real_cargo if {
	denied_cargo("$HOME/.local/bin/bd remember --key k \"note\" && cargo test")
}

# --- (e) command position only (2026-10-02) ----------------------------------
#
# A `git commit -m "..."` whose message described a `cargo build` was denied: the
# guard read words inside quoted arguments as commands. Only a cargo a shell would
# run counts now.

test_allow_git_commit_message_naming_cargo_build if {
	allowed("git commit -m \"fix: cargo build now passes\"")
}

# The text exemption this replaced gave up on a message holding a parenthesis.
test_allow_git_commit_message_with_parens_naming_cargo_build if {
	allowed("git commit -m \"fix(cupcake): a bare cargo build (no -p) is still denied\"")
}

test_allow_echo_single_quoted_cargo_build if {
	allowed("echo 'cargo build'")
}

test_allow_echo_double_quoted_cargo_build_with_separators if {
	allowed("echo \"step one; cargo build --release && done\"")
}

test_allow_unquoted_operand_cargo_build if {
	allowed("echo cargo build")
}

# A commit message written through a heredoc, raw (as opa test sees it) and in the
# `; `-joined shape scripts/cupcake-hook.sh delivers to the engine.
test_allow_git_commit_heredoc_naming_cargo_build if {
	allowed("git commit -F - <<'EOF'\nfix: guard\n\ncargo build --release no longer trips it\nEOF")
}

test_allow_git_commit_heredoc_shim_shape_naming_cargo_build if {
	allowed("git commit -F - <<'EOF'; fix: guard; ; cargo build --release no longer trips it; EOF")
}

test_allow_cat_heredoc_into_file_naming_cargo_build if {
	allowed("cat > /tmp/msg <<'EOF'; cargo build; cargo test --workspace; EOF")
}

# A heredoc a shell reads is a program.
test_deny_heredoc_fed_to_bash if {
	denied_cargo("bash <<'EOF'; cargo build; EOF")
}

# A build after the heredoc terminator is outside the body.
test_deny_cargo_after_heredoc_terminator if {
	denied_cargo("cat > /tmp/msg <<'EOF'; text; EOF; cargo build")
}

# A real build chained after a commit message that names one is still a build.
test_deny_git_commit_then_bare_cargo_build if {
	denied_cargo("git commit -m \"fix: cargo build now passes\" && cargo build --release")
}

test_deny_cargo_in_double_quoted_bash_c if {
	denied_cargo("bash -c \"cd /tmp && cargo build\"")
}

test_deny_cargo_in_command_substitution if {
	denied_cargo("echo \"$(cargo build)\"")
}

test_deny_cargo_after_env_assignment if {
	denied_cargo("RUSTFLAGS=-Dwarnings cargo clippy --all-targets")
}

test_deny_cargo_after_cd_and_timeout if {
	denied_cargo("cd /repo && timeout 30 cargo test")
}

test_deny_cargo_in_subshell if {
	denied_cargo("(cd /repo && cargo check)")
}

test_deny_workspace_flag_closing_a_subshell if {
	denied_cargo("(cargo test -p er-gfx --workspace)")
}

test_deny_cargo_in_brace_group if {
	denied_cargo("{ cargo build; }")
}

test_deny_cargo_with_toolchain if {
	denied_cargo("cargo +nightly build")
}

test_deny_quoted_program_name if {
	denied_cargo("\"cargo\" build")
}

# The scope is per invocation: a `-p` on one cargo does not cover another.
test_deny_second_unscoped_invocation if {
	denied_cargo("cargo test -p er-gfx && cargo build")
}

# A `--all` after `--` belongs to the test binary, not to cargo.
test_allow_all_after_dashdash if {
	allowed("cargo test -p er-gfx -- --all")
}

# ...and an unquoted token in a bd command is an operand of bd, not a program.
test_allow_bd_with_unquoted_cargo_operand if {
	allowed("$HOME/.local/bin/bd close x --reason cargo test")
}

# --- line continuations --------------------------------------------------------
# A backslash-newline joins two lines into one command, so a `-p` on the last
# line scopes the cargo on the first. `scripts/test-cupcake-hook-shim.py` carries
# the same command as allow-line-continuation-splitting-one-command.
test_allow_continuation_split_xwin_build_with_p if {
	allowed("cargo xwin build --release \\\n  --target x86_64-pc-windows-msvc \\\n  -p er-quickload")
}

test_allow_continuation_split_with_crlf if {
	allowed("cargo xwin build --release \\\r\n  -p er-quickload")
}

test_deny_continuation_split_build_without_p if {
	denied_cargo("cargo build \\\n  --release")
}

test_deny_continuation_split_xwin_build_without_p if {
	denied_cargo("cargo xwin build --release \\\n  --target x86_64-pc-windows-msvc")
}

# An escaped backslash before the newline is a literal backslash followed by a
# real line break, so the `-p` on the next line is a command of its own.
test_deny_escaped_backslash_is_not_a_continuation if {
	denied_cargo("cargo build --release \\\\\n-p er-quickload")
}

test_deny_plain_newline_is_still_a_boundary if {
	denied_cargo("cargo build --release\n-p er-quickload")
}
