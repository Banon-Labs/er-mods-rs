# OPA unit tests for git_commit_authorization: no commit, except one the user
# authorized with an exact `authorize commit` reply.
#
# Run with:
#   opa test .cupcake/system/commands.rego \
#            .cupcake/policies/claude/git_commit_authorization.rego \
#            .cupcake/tests/git_commit_authorization_test.rego
#
# The verdict on the user's message, the subagent check and the spent record is
# the `commit_authorization` signal's; these cases feed it the line that signal
# prints. What it prints for a given transcript is tested against fixture JSONL by
# scripts/test-commit-authorization-signal.py.
package cupcake.policies.claude.git_commit_authorization_test

import rego.v1

import data.cupcake.policies.claude.git_commit_authorization as guard

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

authorized := concat("\t", ["AUTHORIZED", "LIVE", "6a1f-uuid"])

denied(reason) := concat("\t", ["DENIED", "DENIED", reason])

denials(e) := {d | some d in guard.deny with input as e}

has_commit_denial(e) if {
	some d in denials(e)
	d.rule_id == "ER-EFFECTS-COMMIT-NEEDS-AUTHORIZATION"
}

reasons(e) := concat(" ", [d.reason | some d in denials(e)])

# --- allowed ------------------------------------------------------------------

test_authorized_plain_commit_allows if {
	not has_commit_denial(event("git commit -m 'feat: x'", authorized))
}

test_authorized_git_c_commit_allows if {
	not has_commit_denial(event("git -C /home/u/repo/.worktrees/w commit -F /tmp/msg", authorized))
}

test_signal_object_form_is_read if {
	not has_commit_denial(event("git commit -F msg.txt", {"output": authorized}))
}

test_non_commit_is_untouched if {
	not has_commit_denial(no_signal_event("git status"))
	not has_commit_denial(no_signal_event("git log --oneline -5"))
}

test_commit_named_in_prose_is_untouched if {
	not has_commit_denial(no_signal_event(`bd remember --key k "never git commit in a worktree"`))
	not has_commit_denial(no_signal_event(`echo "git commit"`))
}

# --- denied: no or bad authorization ------------------------------------------

test_no_signal_denies if {
	has_commit_denial(no_signal_event("git commit -m x"))
}

test_empty_signal_denies if {
	has_commit_denial(event("git commit -m x", ""))
}

test_unauthorized_denies_and_names_the_phrase if {
	e := event("git commit -m x", denied("the user's last message is not `authorize commit`"))
	has_commit_denial(e)
	contains(reasons(e), "`authorize commit`")
	contains(reasons(e), "not `authorize commit`")
}

test_subagent_denial_reason_is_carried if {
	e := event("git commit -m x", denied("a subagent never commits"))
	contains(reasons(e), "a subagent never commits")
}

test_lint_live_alone_does_not_authorize_a_commit if {
	has_commit_denial(event("git commit -m x", concat("\t", ["-", "LIVE", "uuid"])))
}

test_git_c_commit_denies_without_authorization if {
	has_commit_denial(no_signal_event("git -C /home/u/repo/.worktrees/w commit -m x"))
}

test_global_option_forms_deny if {
	has_commit_denial(no_signal_event("git -c core.hooksPath=/dev/null commit -m x"))
	has_commit_denial(no_signal_event("git --no-pager commit -m x"))
	has_commit_denial(no_signal_event("command git commit -m x"))
}

# --- denied even when the signal says AUTHORIZED --------------------------------

test_chained_commit_denies_even_authorized if {
	has_commit_denial(event("git add -A && git commit -m x", authorized))
	has_commit_denial(event("git commit -m x; git commit -m y", authorized))
	has_commit_denial(event("cargo fmt -p x && git commit -m x", authorized))
}

test_wrapped_commit_denies_even_authorized if {
	has_commit_denial(event("bash -c 'git commit -m x'", authorized))
}

test_wrapped_commit_denies_without_authorization if {
	has_commit_denial(no_signal_event("bash -c 'git commit -m x'"))
	has_commit_denial(no_signal_event("cd /tmp/w && git commit -am x"))
}

test_opaque_payload_naming_commit_denies if {
	has_commit_denial(event("bash -c $CMD git commit", authorized))
}

test_malformed_signal_denies if {
	has_commit_denial(event("git commit -m x", "AUTHORIZED"))
	has_commit_denial(event("git commit -m x", "AUTHORIZED\tLIVE\tuuid\textra"))
}
