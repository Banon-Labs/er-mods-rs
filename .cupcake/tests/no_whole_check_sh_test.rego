# OPA unit tests for no_whole_check_sh.
# Run with:
#   opa test .cupcake/system/commands.rego \
#     .cupcake/policies/claude/no_whole_check_sh.rego \
#     .cupcake/tests/no_whole_check_sh_test.rego
package cupcake.policies.claude.no_whole_check_sh_test

import rego.v1

import data.cupcake.policies.claude.no_whole_check_sh as guard

bash_event(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "timeout": 30000},
	"signals": {},
}

rule_ids(denials) := {d.rule_id | some d in denials}

denied(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	"ER-EFFECTS-NO-CHECK-SH" in rule_ids(denials)
}

allowed(cmd) if not denied(cmd)

# --- the command the user watched pin every core under their game ------------
test_deny_bare_run if {
	denied("bash scripts/check.sh")
}

test_deny_absolute_path if {
	denied("bash /home/banon/projects/er-mods-rs/scripts/check.sh")
}

test_deny_dot_slash if {
	denied("sh ./scripts/check.sh")
}

test_deny_invoked_directly if {
	denied("scripts/check.sh")
}

test_deny_redirected_to_a_file if {
	denied("bash scripts/check.sh > /tmp/out.txt 2>&1")
}

test_deny_backgrounded if {
	denied("bash scripts/check.sh &")
}

# --- and every form that used to slip past a position-anchored guard ---------
test_deny_second_in_a_chain if {
	denied("cargo fmt -p er-invasion-warp && bash scripts/check.sh")
}

test_deny_wrapped_in_bash_dash_c if {
	denied("bash -c 'bash scripts/check.sh'")
}

test_deny_with_a_timeout_wrapper if {
	denied("timeout 115 bash scripts/check.sh")
}

test_deny_with_an_env_prefix if {
	denied("ER_CHECK_JOBS=4 bash scripts/check.sh")
}

# --- the stage forms are denied too. The user removed that exemption ---------
# "How about never run check.sh period?", 2026-09-18.
test_deny_single_stage if {
	denied("bash scripts/check.sh --stage lint")
}

test_deny_list_stages if {
	denied("bash scripts/check.sh --list-stages")
}

test_deny_force_override if {
	denied("ER_CHECK_FORCE=1 bash scripts/check.sh")
}

# --- naming the command is not running it ------------------------------------
# The half `require_scoped_cargo` declared unwritable: a guard on this script must
# not block the commit, the memory or the doc that describes it, or the edit that
# would remove the guard cannot be committed in the repo that enforces it.
test_allow_commit_message_naming_it if {
	allowed(`git commit -m "guard: the agent no longer runs bash scripts/check.sh"`)
}

test_allow_bd_memory_body_naming_it if {
	allowed(`$HOME/.local/bin/bd remember --key k "the agent used to run
bash scripts/check.sh
and it cost ten minutes"`)
}

test_allow_echo_of_the_command if {
	allowed(`echo "bash scripts/check.sh"`)
}

test_allow_heredoc_documenting_it if {
	allowed(`cat > docs/gates.md <<'EOF'
bash scripts/check.sh
EOF`)
}

# --- and the gates the agent is supposed to run instead ----------------------
test_allow_scoped_cargo_test if {
	allowed("cargo test -p er-invasion-warp")
}

test_allow_a_named_gate_script if {
	allowed("python3 scripts/check-prologue-bytes.py")
}

test_allow_a_gate_whose_name_merely_ends_in_check if {
	allowed("python3 scripts/check-comment-caps.py crates/er-invasion-warp/src/lib.rs")
}

test_allow_reading_the_script if {
	allowed("python3 -c \"print(open('scripts/check.sh').read())\"")
}
