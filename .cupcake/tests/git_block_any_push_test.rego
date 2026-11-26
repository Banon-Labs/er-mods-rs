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

# --- the watched-push helper: a run is refused, a mention is not (2026-11-24) -------
#
# The helper used to be refused on the substring `er-push-watched` anywhere in the
# command. Two read-only commands were refused that way the same day: a python
# printer handed `scripts/er-push-watched.sh:34` as an argument, and a `sed -n` print
# of the helper's own test script. Each run spelling below must still deny, whatever
# the signal says, and each mention must not.

watched_denied(cmd) if has_push_denial(push_event(cmd, authorized))

test_watched_helper_run_directly_denies if {
	watched_denied("scripts/er-push-watched.sh feat/x feat/x origin")
	watched_denied("./scripts/er-push-watched.sh feat/x feat/x origin")
	watched_denied("/home/banon/projects/er-mods-rs/scripts/er-push-watched.sh feat/x feat/x origin")
}

test_watched_helper_run_through_a_shell_denies if {
	watched_denied("sh scripts/er-push-watched.sh feat/x")
	watched_denied("/usr/bin/bash scripts/er-push-watched.sh feat/x")
	watched_denied("bash -euo pipefail scripts/er-push-watched.sh feat/x")
	watched_denied("bash \"scripts/er-push-watched.sh\" feat/x")
	watched_denied("source scripts/er-push-watched.sh feat/x")
	watched_denied(". scripts/er-push-watched.sh feat/x")
}

test_watched_helper_run_behind_prefixes_denies if {
	watched_denied("FOO=1 bash scripts/er-push-watched.sh feat/x")
	watched_denied("env FOO=1 bash scripts/er-push-watched.sh feat/x")
	watched_denied("timeout 3600 bash scripts/er-push-watched.sh feat/x")
	watched_denied("setsid nohup bash scripts/er-push-watched.sh feat/x &")
	watched_denied("time bash scripts/er-push-watched.sh feat/x")
	watched_denied("echo feat/x | xargs bash scripts/er-push-watched.sh")
	watched_denied("echo feat/x | xargs -r scripts/er-push-watched.sh")
}

# `bash -n` reads a script without running it, but this guard does not carry that
# exemption, so the syntax check is refused with the run. Pinned so a change to it is
# a decision, not a drift.
test_watched_helper_syntax_check_still_denies if {
	watched_denied("bash -n scripts/er-push-watched.sh")
}

test_watched_helper_run_after_a_separator_denies if {
	watched_denied("git fetch origin && bash scripts/er-push-watched.sh feat/x")
	watched_denied("cd /home/banon/projects/er-mods-rs; scripts/er-push-watched.sh feat/x")
	watched_denied("git status || bash scripts/er-push-watched.sh feat/x")
	watched_denied("git status\nbash scripts/er-push-watched.sh feat/x")
	watched_denied("bash scripts/er-push-watched.sh feat/x | python3 scripts/monitor-throttle.py 15")
	watched_denied("(bash scripts/er-push-watched.sh feat/x)")
}

test_watched_helper_run_inside_a_wrapper_payload_denies if {
	watched_denied("bash -c 'bash scripts/er-push-watched.sh feat/x'")
	watched_denied("bash -c \"cd /x && scripts/er-push-watched.sh feat/x\"")
}

# A shell payload the guard cannot read that names the helper is refused, as before.
test_watched_helper_named_in_an_unreadable_payload_denies if {
	watched_denied("bash -c $RUN scripts/er-push-watched.sh")
	watched_denied("eval $RUN scripts/er-push-watched.sh")
}

# A program named by a variable may be a shell, so the helper behind one is a run.
test_watched_helper_behind_a_variable_program_denies if {
	watched_denied("$SHELL scripts/er-push-watched.sh feat/x")
	watched_denied("bash -c \"$CMD scripts/er-push-watched.sh\"")
}

test_watched_helper_mentions_are_allowed if {
	not watched_denied("python3 /tmp/reflow.py scripts/er-push-watched.sh:34")
	not watched_denied("sed -n '20,60p' scripts/test-er-push-watched-ref-carries-work.sh")
	not watched_denied("sed -n '1,40p' scripts/er-push-watched.sh")
	not watched_denied("cat scripts/er-push-watched.sh")
	not watched_denied("python3 -c 'print(1)' scripts/er-push-watched.sh")
	not watched_denied("python3 scripts/reflow.py --width 100 scripts/er-push-watched.sh")
	not watched_denied("cat > /tmp/notes.md <<'EOF'\nnever run bash scripts/er-push-watched.sh (it pushes)\nEOF")
	not watched_denied("cat > /tmp/notes.md <<'EOF' never run bash scripts/er-push-watched.sh (it pushes) EOF")
	not watched_denied("git log --oneline -- scripts/er-push-watched.sh")
	not watched_denied("git diff main -- scripts/er-push-watched.sh && sed -n '1,20p' scripts/er-push-watched.sh")
	not watched_denied("bash scripts/test-er-push-watched-ref-carries-work.sh")
	not watched_denied("git commit -m \"docs: never run bash scripts/er-push-watched.sh\"")
	not watched_denied("rg -n 'er-push-watched' scripts/")
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
