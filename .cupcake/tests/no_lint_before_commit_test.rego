# OPA unit tests for no_lint_before_commit: lints and formatters run only while an
# unspent `authorize commit` from the user is live.
#
# Run with:
#   opa test .cupcake/system/commands.rego \
#            .cupcake/policies/claude/no_lint_before_commit.rego \
#            .cupcake/tests/no_lint_before_commit_test.rego
#
# Whether an authorization is live is the `commit_authorization` signal's answer
# (its second field); scripts/test-commit-authorization-signal.py tests that
# against fixture transcripts. These cases test what counts as a lint run.
package cupcake.policies.claude.no_lint_before_commit_test

import rego.v1

import data.cupcake.policies.claude.no_lint_before_commit as guard

event(command, signal) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": command, "timeout": 30000},
	"signals": {"commit_authorization": signal},
}

no_signal_event(command) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": command, "timeout": 30000},
}

live := concat("\t", ["-", "LIVE", "6a1f-uuid"])

not_live := concat("\t", ["-", "DENIED", "the user's last message is not `authorize commit`"])

subagent := concat("\t", ["-", "DENIED", "a subagent never commits and never runs lints or formatters"])

denies(command) if {
	some d in guard.deny with input as event(command, not_live)
	d.rule_id == "ER-EFFECTS-NO-LINT-BEFORE-COMMIT"
}

allows_unauthorized(command) if {
	count({d | some d in guard.deny with input as event(command, not_live)}) == 0
}

allows_live(command) if {
	count({d | some d in guard.deny with input as event(command, live)}) == 0
}

lint_commands := [
	"cargo fmt --all",
	"cargo fmt -p er-quickload -- --check",
	"cargo +nightly fmt",
	"cargo clippy -p er-quickload --all-targets",
	"cargo xwin clippy -p er-quickload --target x86_64-pc-windows-msvc",
	"~/.cargo/bin/cargo clippy -p x",
	"rustfmt crates/er-quickload/src/lib.rs",
	"shellcheck scripts/run-camera-smoke.sh",
	"ruff check scripts",
	"uvx ruff format scripts",
	"uv run --with ruff ruff check .",
	"black scripts",
	"python3 -m black scripts",
	"npx prettier --write docs",
	"pnpm exec eslint .",
	"pipx run black .",
	"bash scripts/check.sh",
	"bash scripts/check.sh --stage lint",
	"bash scripts/act-check.sh --stage lint",
	"python3 scripts/check-comment-caps.py crates/x/src/lib.rs",
	"python3 scripts/check-comment-caps.py --fix crates/x/src/lib.rs",
	"python3 /home/banon/projects/er-mods-rs/scripts/check-comment-caps.py",
	"python3 scripts/check-no-lossy-utf8.py",
	"python3 scripts/some-sweep.py --fix",
	"cargo clippy --fix -p x",
	"cargo test -p x && cargo fmt -p x",
	"bash -c 'cargo fmt -p x'",
	"timeout 20 cargo clippy -p x",
]

test_every_lint_command_denies_without_authorization if {
	every command in lint_commands {
		denies(command)
	}
}

test_every_lint_command_allows_while_authorization_is_live if {
	every command in lint_commands {
		allows_live(command)
	}
}

test_no_signal_denies if {
	some d in guard.deny with input as no_signal_event("cargo fmt -p x")
	d.rule_id == "ER-EFFECTS-NO-LINT-BEFORE-COMMIT"
}

test_subagent_denies_and_says_so if {
	some d in guard.deny with input as event("cargo fmt -p x", subagent)
	contains(d.reason, "a subagent never")
}

test_reason_names_the_phrase if {
	some d in guard.deny with input as event("cargo fmt -p x", not_live)
	contains(d.reason, "`authorize commit`")
}

test_commit_authorized_field_alone_does_not_open_lints if {
	some d in guard.deny with input as event("cargo fmt -p x", concat("\t", ["AUTHORIZED", "DENIED", "spent"]))
	d.rule_id == "ER-EFFECTS-NO-LINT-BEFORE-COMMIT"
}

# --- not lints ------------------------------------------------------------------

not_lint_commands := [
	"cargo test -p er-quickload",
	"cargo check -p er-quickload",
	"cargo xwin build --release --target x86_64-pc-windows-msvc -p er-quickload",
	"python3 scripts/check-comment-caps.py --selftest",
	"python3 scripts/check-no-lossy-utf8.py --selftest",
	"opa test .cupcake/policies/claude/no_lint_before_commit.rego .cupcake/tests/no_lint_before_commit_test.rego",
	"python3 scripts/test-commit-authorization-signal.py",
	"python3 scripts/er-mechanics-ash-table.py --build x",
	"python3 scripts/check-dump-deobf-identity.py 0x14074a970",
	"git status",
	`git commit -m 'run cargo fmt and black before the next one'`,
	`bd remember --key k "lints wait: cargo fmt, cargo clippy, check-comment-caps.py --fix"`,
	`echo "shellcheck ruff prettier --fix"`,
	"grep -n clippy Cargo.toml",
	"cat > /tmp/x.md <<'EOF'\nrun cargo fmt -p x\nEOF",
]

test_non_lint_commands_are_untouched if {
	every command in not_lint_commands {
		allows_unauthorized(command)
	}
}

# A lint script named as an operand is not a lint run (2026-10-19): `git apply
# --exclude=scripts/check.sh <patch>` was refused as if it ran the suite.
operand_lint_paths := [
	"git apply --exclude=scripts/check.sh /tmp/x.patch",
	"cd /home/banon/projects/er-mods-rs && git apply --exclude=scripts/check.sh /tmp/x.patch",
	"git -C /x apply --exclude scripts/check.sh --exclude scripts/check-comment-caps.py p.patch",
	"git add scripts/check.sh scripts/act-check.sh",
	"git diff -- scripts/check-no-lossy-utf8.py",
	"git show HEAD:scripts/check.sh",
	"cat scripts/check.sh",
	"sed -n 1,40p scripts/check-comment-caps.py",
	"python3 -c \"print(open('scripts/check.sh').read())\"",
	# The command actually refused, verbatim: the assignment is the first word of its
	# statement and its last path part is `check.sh`.
	`cd /home/banon/projects/er-mods-rs; P=/tmp/x/run-stamp.patch; X="--exclude=scripts/check.sh --exclude=docs/ci-gate-portability.tsv --exclude=AGENTS.md"; git apply --check $X "$P" && git apply $X "$P" && echo applied`,
	"X=scripts/check-comment-caps.py; git add $X",
	"EXCLUDES='--exclude=a --exclude=scripts/check.sh'; git apply $EXCLUDES p.patch",
]

test_lint_script_named_as_an_operand_is_untouched if {
	every command in operand_lint_paths {
		allows_unauthorized(command)
	}
}

executing_lint_paths := [
	"./scripts/check.sh",
	"exec bash scripts/check.sh",
	"source scripts/check.sh",
	". scripts/check.sh",
	"git apply --exclude=scripts/check.sh p.patch && bash scripts/check.sh",
	"git apply p.patch; ./scripts/check.sh --stage lint",
	"git status | bash scripts/act-check.sh",
	"git status || python3 scripts/check-comment-caps.py x.rs",
	"git status\nbash scripts/check.sh",
	"P=/tmp/x.patch bash scripts/check.sh",
	"OUT=/tmp/x cargo fmt -p x",
]

test_executing_lint_script_denies if {
	every command in executing_lint_paths {
		denies(command)
	}
}
