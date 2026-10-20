# OPA unit tests for git_block_any_push: no push, except one the user authorized
# with an exact `authorize <sha>` reply.
#
# Run with:
#   opa test .cupcake/system/commands.rego .cupcake/policies/claude/git_block_any_push.rego \
#            .cupcake/tests/git_block_any_push_test.rego
#
# The verdict on the user's message, the branch tip and the spent-authorization
# record is the `push_authorization` signal's, and these cases feed it the line
# that signal prints for each situation. What the signal prints for a given
# transcript is tested against fixture JSONL by
# scripts/test-push-authorization-signal.py.
#
# The last section pins the heredoc shapes measured 2026-10-16, where a push hid
# behind a quoted-tag commit message carrying a backtick.
package cupcake.policies.claude.git_block_any_push_test

import rego.v1

import data.cupcake.policies.claude.git_block_any_push as guard

tip := "0123456789abcdef0123456789abcdef01234567"

push_event(command, signal) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": command, "timeout": 30000},
	"signals": {"push_authorization": signal},
}

no_signal_event(command) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": command, "timeout": 30000},
}

authorized := concat("\t", ["AUTHORIZED", "feat/x", tip, "6a1f-uuid"])

denied(reason) := concat("\t", ["DENIED", "feat/x", tip, reason])

denials(event) := {d | some d in guard.deny with input as event}

has_push_denial(event) if {
	some d in denials(event)
	d.rule_id == "ER-EFFECTS-BLOCK-ANY-PUSH"
}

reasons(event) := concat(" ", [d.reason | some d in denials(event)])

# --- allowed: the one shape ----------------------------------------------------

test_exact_authorization_allows_the_push if {
	not has_push_denial(push_event("git push origin feat/x", authorized))
}

test_exact_authorization_allows_set_upstream if {
	not has_push_denial(push_event("git push -u origin feat/x", authorized))
}

test_exact_authorization_allows_git_c if {
	not has_push_denial(push_event("git -C /home/u/repo push origin feat/x", authorized))
}

test_signal_object_form_is_read if {
	not has_push_denial(push_event("git push origin feat/x", {"output": authorized}))
}

# --- denied: no or bad authorization -------------------------------------------

test_no_signal_denies if {
	has_push_denial(no_signal_event("git push origin feat/x"))
}

test_empty_signal_denies if {
	has_push_denial(push_event("git push origin feat/x", ""))
}

test_no_authorization_denies_and_names_the_phrase if {
	event := push_event("git push origin feat/x", denied("the user's last message is not an authorization"))
	has_push_denial(event)
	contains(reasons(event), concat("", ["`authorize ", tip, "`"]))
	contains(reasons(event), "`feat/x`")
}

test_extra_text_denies if {
	has_push_denial(push_event("git push origin feat/x", denied("the user's last message is not an authorization")))
}

test_wrong_sha_denies if {
	event := push_event("git push origin feat/x", denied("the user authorized deadbee, which is not the tip of `feat/x`"))
	has_push_denial(event)
	contains(reasons(event), concat("", ["authorize ", tip]))
}

test_sha_of_another_branch_denies if {
	has_push_denial(push_event("git push origin feat/x", denied("the user authorized 1111111, which is not the tip of `feat/x`")))
}

test_reused_authorization_denies if {
	event := push_event("git push origin feat/x", denied("that authorization was already spent on another push"))
	has_push_denial(event)
	contains(reasons(event), "already spent")
}

test_authorization_only_in_assistant_text_denies if {
	has_push_denial(push_event("git push origin feat/x", denied("the user's last message is not an authorization")))
}

test_authorization_only_in_tool_result_denies if {
	has_push_denial(push_event("git push origin feat/x", denied("no message typed by the user precedes this tool call")))
}

test_unknown_verdict_denies if {
	has_push_denial(push_event("git push origin feat/x", concat("\t", ["ALLOWED", "feat/x", tip, "x"])))
}

test_truncated_authorized_line_denies if {
	has_push_denial(push_event("git push origin feat/x", "AUTHORIZED"))
}

# --- denied whatever the signal says --------------------------------------------

test_main_denies_even_when_signal_says_authorized if {
	has_push_denial(push_event("git push origin main", authorized))
}

test_main_denial_without_tip_gives_general_reason if {
	event := push_event("git push origin main", "DENIED\tmain\t-\t`main` is never pushed by the agent")
	has_push_denial(event)
	contains(reasons(event), "never pushed by the agent")
	not contains(reasons(event), "authorize -")
}

test_force_denies_even_when_signal_says_authorized if {
	has_push_denial(push_event("git push --force origin feat/x", authorized))
}

test_force_with_lease_denies if {
	has_push_denial(push_event("git push --force-with-lease origin feat/x", authorized))
}

test_short_force_denies if {
	has_push_denial(push_event("git push -f origin feat/x", authorized))
}

test_plus_refspec_denies if {
	has_push_denial(push_event("git push origin +feat/x", authorized))
}

test_colon_refspec_denies if {
	has_push_denial(push_event("git push origin feat/x:main", authorized))
}

test_delete_denies if {
	has_push_denial(push_event("git push origin --delete feat/x", authorized))
}

test_tags_and_mirror_and_all_deny if {
	has_push_denial(push_event("git push --tags origin feat/x", authorized))
	has_push_denial(push_event("git push --mirror origin", authorized))
	has_push_denial(push_event("git push --all origin", authorized))
}

test_second_push_in_same_command_denies if {
	has_push_denial(push_event("git push origin feat/x; git push origin other", authorized))
}

test_wrapped_push_denies if {
	has_push_denial(push_event("bash -c \"git push origin feat/x\"", authorized))
}

test_watched_push_helper_denies if {
	has_push_denial(push_event("bash scripts/er-push-watched.sh feat/x", authorized))
}

# --- not a push -------------------------------------------------------------------

test_non_push_command_is_untouched if {
	not has_push_denial(push_event("git status", ""))
}

test_commit_mentioning_push_is_untouched if {
	not has_push_denial(push_event("git commit -m \"docs: explain git push rules\"", ""))
}

# --- a push behind a literal heredoc body (2026-10-16) ----------------------------
#
# A commit message written through a quoted-tag heredoc whose body carries a backtick
# used to leave the command unread end to end: the hook shim gave up on the backtick,
# so the newline after the terminator was never turned into `; `, and the push behind
# it arrived with no command position. These pin the policy side of that hole on the
# text the fixed shim delivers; the shim side is pinned in
# scripts/test-cupcake-hook-shim.py. No signal is fed, so a push read as one denies.

push_main := concat(" ", ["git", "push", "origin", "main"])

heredoc_push_denied(cmd) if has_push_denial(no_signal_event(cmd))

message := "fix: quote `echo $(date)` in the message"

test_push_after_a_backtick_heredoc_body_is_denied_as_delivered if {
	heredoc_push_denied(concat("", ["git commit -F - <<'EOF' ", message, " EOF; ", push_main]))
}

# The same text with no shim in front: the newline after the terminator arrives as a space.
test_push_after_a_backtick_heredoc_body_is_denied_without_the_shim if {
	heredoc_push_denied(concat("", ["git commit -F - <<'EOF' ", message, " EOF ", push_main]))
}

test_push_after_a_backtick_heredoc_body_is_denied_by_lines if {
	heredoc_push_denied(concat("", ["git commit -F - <<'EOF'\n", message, "\nEOF\n", push_main]))
}

test_push_on_the_opener_line_is_denied if {
	heredoc_push_denied(concat("", ["git commit -F - <<'EOF' ; ", push_main, " ", message, " EOF"]))
	heredoc_push_denied(concat("", ["git commit -F - <<'EOF' ; ", push_main, "\n", message, "\nEOF"]))
}

test_push_quoted_inside_a_literal_body_is_allowed if {
	not heredoc_push_denied(concat("", ["git commit -F - <<'EOF' never run `", push_main, "` here EOF"]))
	not heredoc_push_denied(concat("", ["git commit -F - <<'EOF'\nnever run `", push_main, "` here\nEOF"]))
}

test_plain_commit_is_allowed if {
	not heredoc_push_denied(concat("", ["git commit -F - <<'EOF' ", message, " EOF"]))
}
