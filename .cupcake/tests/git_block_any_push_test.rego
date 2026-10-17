# OPA unit tests for git_block_any_push, for the heredoc shapes measured 2026-10-16.
#
# A commit message written through a quoted-tag heredoc whose body carries a backtick used to
# leave the command unread end to end: the hook shim gave up on the backtick, so the newline after
# the terminator was never turned into `; `, and the push behind it arrived with no command position.
# These pin the policy side of that hole on the text the fixed shim delivers; the shim side is pinned
# in scripts/test-cupcake-hook-shim.py.
#
# Run with:
#   opa test .cupcake/system/commands.rego \
#     .cupcake/policies/claude/git_block_any_push.rego \
#     .cupcake/tests/git_block_any_push_test.rego
package cupcake.policies.claude.git_block_any_push_test

import rego.v1

import data.cupcake.policies.claude.git_block_any_push as guard

push_main := concat(" ", ["git", "push", "origin", "main"])

bash(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd},
}

denied(cmd) if {
	some d in guard.deny with input as bash(cmd)
	d.rule_id == "ER-EFFECTS-BLOCK-ANY-PUSH"
}

message := "fix: quote `echo $(date)` in the message"

test_push_after_a_backtick_heredoc_body_is_denied_as_delivered if {
	denied(concat("", ["git commit -F - <<'EOF' ", message, " EOF; ", push_main]))
}

# The same text with no shim in front: the newline after the terminator arrives as a space.
test_push_after_a_backtick_heredoc_body_is_denied_without_the_shim if {
	denied(concat("", ["git commit -F - <<'EOF' ", message, " EOF ", push_main]))
}

test_push_after_a_backtick_heredoc_body_is_denied_by_lines if {
	denied(concat("", ["git commit -F - <<'EOF'\n", message, "\nEOF\n", push_main]))
}

test_push_on_the_opener_line_is_denied if {
	denied(concat("", ["git commit -F - <<'EOF' ; ", push_main, " ", message, " EOF"]))
	denied(concat("", ["git commit -F - <<'EOF' ; ", push_main, "\n", message, "\nEOF"]))
}

test_push_quoted_inside_a_literal_body_is_allowed if {
	not denied(concat("", ["git commit -F - <<'EOF' never run `", push_main, "` here EOF"]))
	not denied(concat("", ["git commit -F - <<'EOF'\nnever run `", push_main, "` here\nEOF"]))
}

test_plain_commit_is_allowed if {
	not denied(concat("", ["git commit -F - <<'EOF' ", message, " EOF"]))
}
