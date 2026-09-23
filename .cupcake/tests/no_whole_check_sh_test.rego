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

# --- ...and that same heredoc in the shape production actually delivers -------
# The case above passes without this file's heredoc handling, because
# `commands.heredoc_body_blanked` resolves it in the opa interpreter: the raw text
# still has the newline in front of the terminator that it looks for. The engine
# deletes every unquoted newline before a policy runs, so what production hands this
# rule is the body welded onto the command that writes it -- one line, with the body's
# own punctuation standing as command positions. These cases are written in that shape
# on purpose; in the multi-line spelling they would prove nothing.
#
# The first is the measured false positive of 2026-09-22, abridged: a pull-request body
# written to a file, denied for a parenthesised aside naming the script.
test_allow_delivered_pr_body_heredoc_naming_it if {
	allowed("cat > /tmp/scratch/pr444-body.md <<'EOF' ## Gates run test-check-sh-lock.py PASS (13/13) test-check-sh-accumulates.py PASS (check.sh changed) EOF timeout 60 gh pr edit 444 --body-file /tmp/scratch/pr444-body.md")
}

test_allow_delivered_heredoc_naming_a_full_invocation if {
	allowed("cat > /tmp/scratch/notes.md <<'EOF' The agent does not run it; bash scripts/check.sh is the pre-push hook's job. EOF")
}

test_allow_delivered_heredoc_appended_to_a_file if {
	allowed("cat >> docs/gates.md <<'EOF' the suite (bash scripts/check.sh runs in CI) not here EOF")
}

# A commit message is a heredoc with no redirect in it at all, which is why the
# exemption asks who is fed the body rather than where the body goes. The first draft
# of this rule required a `>` and refused the commit that introduced it.
test_allow_delivered_commit_message_heredoc if {
	allowed("git commit -F - <<'EOF' the guard denies it now; bash scripts/check.sh is the hook's job EOF")
}

# A body whose first line is a markdown table opens with a separator, and that is still
# prose: the word after it is not a token a command line can hold, so the scan for a
# shell on the opener's line stops there.
test_allow_delivered_heredoc_body_opening_with_a_table if {
	allowed("cat > /tmp/scratch/pr.md <<'EOF' | gate | result | |---|---| | the suite | (check.sh changed) | EOF")
}

# The terminator as the delivered text carried it in the measured case: a newline the
# engine kept glued `EOF` to the word before it and to the command after it, so the
# span can only be closed by reading a word as its lines.
test_allow_delivered_heredoc_whose_terminator_is_glued_by_a_newline if {
	allowed("cat > /tmp/scratch/pr444-body.md <<'EOF' Draft on purpose (check.sh changed) undrafting is the user's call.\nEOF\ntimeout 60 gh pr edit 444 --body-file /tmp/scratch/pr444-body.md")
}

# --- and the heredoc forms that are still a program --------------------------
# A body a shell reads is not prose, and nor is a statement standing on the opener's
# own line. The terminator ends the exemption: what follows it is an ordinary command.
test_deny_heredoc_read_by_a_shell if {
	denied("bash <<'EOF'; bash scripts/check.sh; EOF")
}

test_deny_delivered_heredoc_piped_into_a_shell if {
	denied("cat <<'EOF' | bash bash scripts/check.sh EOF")
}

# The same pipe reached across a line continuation and across a descriptor redirect.
# Both keep the shell on the opener's own line, where the body has not started yet.
test_deny_delivered_heredoc_piped_over_a_continuation if {
	denied("cat <<'EOF' \\ | bash bash scripts/check.sh EOF")
}

test_deny_delivered_heredoc_piped_past_a_redirect if {
	denied("cat > /tmp/scratch/notes.md <<'EOF' 2>&1 | bash bash scripts/check.sh EOF")
}

test_deny_delivered_heredoc_read_by_an_absolute_shell if {
	denied("/bin/bash <<'EOF'; bash scripts/check.sh; EOF")
}

test_deny_delivered_statement_on_the_opener_line if {
	denied("cat > /tmp/scratch/notes.md <<'EOF' ; bash scripts/check.sh ; EOF")
}

test_deny_delivered_run_after_the_terminator if {
	denied("cat > /tmp/scratch/notes.md <<'EOF' documentation only EOF ; bash scripts/check.sh")
}

test_deny_delivered_heredoc_without_a_terminator if {
	denied("cat > /tmp/scratch/notes.md <<'EOF' ; bash scripts/check.sh")
}

# The residue the policy names, pinned so it is a decision rather than a surprise: once
# the newline is gone, a body whose FIRST line opens with a separator is the same text
# as a statement standing on the opener's own line, and the exemption is refused there.
# Denying is the direction this guard errs in.
test_deny_delivered_heredoc_whose_body_opens_with_a_separator if {
	denied("cat >> docs/gates.md <<'EOF' (bash scripts/check.sh runs in CI) not here EOF")
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
