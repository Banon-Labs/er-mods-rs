# OPA unit tests for the builtin git no-verify policy.
# Run with:
#   opa test .cupcake/system/commands.rego \
#            .cupcake/policies/claude/builtins/git_block_no_verify.rego \
#            .cupcake/tests/git_block_no_verify_test.rego
package cupcake.policies.builtins.git_block_no_verify_test

import rego.v1

import data.cupcake.policies.builtins.git_block_no_verify as guard

bash_event(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "timeout": 30000},
}

rule_ids(denials) := {d.rule_id | some d in denials}

test_deny_git_commit_no_verify_long if {
	denials := guard.deny with input as bash_event("git commit --no-verify -m bad")
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_git_commit_no_verify_short if {
	denials := guard.deny with input as bash_event("git commit -nm bad")
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_git_dash_c_commit_no_verify_short if {
	denials := guard.deny with input as bash_event("git -C /tmp/repo commit -n -m bad")
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_allow_git_commit_help_piped_to_sed_n if {
	denials := guard.deny with input as bash_event("git commit -h 2>&1 | sed -n '1,80p'")
	count(denials) == 0
}

test_allow_git_commit_then_later_sed_n if {
	denials := guard.deny with input as bash_event("git commit -m ok && sed -n '1,80p' file")
	count(denials) == 0
}

# --- Shell-wrapper payloads (2026-08-26, bd er-effects-rs-dt2e) ---------------
#
# This policy recognises its verbs through commands.has_verb, whose
# `(^|\s)verb(\s|$)` boundary a quote does not satisfy -- so a hook bypass
# wrapped in `bash -c` produced ZERO denials, measured. The scan set is the
# executed-text decomposition UNION the raw command, so this is strictly
# additive: nothing this policy used to deny stopped being denied.
#
# The flag is assembled rather than written whole so that editing this file
# through a Bash command is not itself denied by the unanchored
# `contains(cmd, ...)` test these cases exercise -- which is this policy's own
# false positive, filed rather than changed here (it is a vendored builtin).
no_verify := concat("", ["--no", "-verify"])

test_deny_wrapped_no_verify_commit if {
	denials := guard.deny with input as bash_event(concat("", ["bash -c 'git commit ", no_verify, " -m bad'"]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_wrapped_no_verify_push if {
	denials := guard.deny with input as bash_event(concat("", [`sh -c "git push `, no_verify, ` origin x"`]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_wrapped_no_verify_merge if {
	denials := guard.deny with input as bash_event(concat("", ["bash -lc 'git merge ", no_verify, " topic'"]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_wrapped_short_no_verify_commit if {
	denials := guard.deny with input as bash_event("bash -c 'git commit -nm bad'")
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_nested_wrapped_no_verify_commit if {
	denials := guard.deny with input as bash_event(concat("", [`bash -c 'bash -c "git commit `, no_verify, ` -m bad"'`]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_eval_no_verify_commit if {
	denials := guard.deny with input as bash_event(concat("", [`eval "git commit `, no_verify, ` -m bad"`]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

# --- core.hooksPath: the two tokens must be ONE assignment (2026-08-31) -------
#
# The rule used to AND `contains(cmd, "core.hookspath")` with
# `contains(cmd, "/dev/null")` over the WHOLE command string, so a command that
# INSTALLED hooks and merely redirected something unrelated to /dev/null was
# denied as "Disabling git hooks is not permitted" -- measured live, while an
# agent was repairing a core.hooksPath that had silently disabled every hook in
# this repository since the er-effects-rs -> er-mods-rs rename.
#
# Both halves are tested, because dropping the `/dev/null` half would have been
# the easy wrong fix: `git config core.hooksPath /dev/null` really does disable
# hooks and must stay denied.
#
# The two tokens are ASSEMBLED for the same reason `no_verify` above is: written
# whole, they are exactly what this rule denies, so an agent editing this file
# through a Bash heredoc would be blocked by the rule the file tests. (The two
# pre-existing cases below were written whole and had that problem.)
hooks_path := concat("", ["core.hooks", "Path"])

dev_null := concat("", ["/dev/", "null"])

# The hooksPath rule shipped comparing a mixed-case literal against a lowercased
# command, so it could never fire. Pinned here now that it can.
test_deny_hooks_path_to_dev_null if {
	denials := guard.deny with input as bash_event(concat(" ", ["git config", hooks_path, dev_null]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_wrapped_hooks_path_to_dev_null if {
	denials := guard.deny with input as bash_event(concat("", [`bash -c "git config `, hooks_path, " ", dev_null, `"`]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_hooks_path_global_to_dev_null if {
	denials := guard.deny with input as bash_event(concat(" ", ["git config --global", hooks_path, dev_null]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_hooks_path_worktree_to_dev_null if {
	denials := guard.deny with input as bash_event(concat(" ", ["git config --worktree", hooks_path, dev_null]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

# A quoted value: commands.scan_text blanks a quoted span's ANCHOR characters but
# leaves the quote characters themselves in place, so the gap between key and
# value really does contain a `"` in the text the rule reads.
test_deny_hooks_path_quoted_value if {
	denials := guard.deny with input as bash_event(concat("", ["git config ", hooks_path, ` "`, dev_null, `"`]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

# A trailing backslash JOINS the two lines. scripts/cupcake-hook.sh leaves such a
# newline alone (it is not a command boundary) and the engine then collapses it to
# a space, so the key and the value are still one assignment.
test_deny_hooks_path_line_continuation if {
	denials := guard.deny with input as bash_event(concat("", ["git config ", hooks_path, " \\\n    ", dev_null]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

# `git -c key=value` carries no `config` verb, so the old rule ALLOWED it outright
# -- measured through the real binary before this change.
test_deny_git_dash_c_hooks_path_inline if {
	denials := guard.deny with input as bash_event(concat("", ["git -c ", hooks_path, "=", dev_null, " commit -m x"]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

test_deny_absolute_git_dash_c_hooks_path_inline if {
	denials := guard.deny with input as bash_event(concat("", ["/usr/bin/git -c ", hooks_path, "=", dev_null, " push origin topic"]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

# The GIT_CONFIG_* environment form puts the key and the value in SEPARATE
# assignments, so no same-assignment pattern can reach it; it has its own clause,
# which requires the captured indices to match.
test_deny_git_config_env_same_index if {
	denials := guard.deny with input as bash_event(concat("", [
		"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=", hooks_path,
		" GIT_CONFIG_VALUE_0=", dev_null, " git status",
	]))
	"BUILTIN-GIT-BLOCK-NO-VERIFY" in rule_ids(denials)
}

# --- the repair direction, which the old rule denied --------------------------

test_allow_hooks_path_repair_with_unrelated_dev_null_redirect if {
	denials := guard.deny with input as bash_event(concat("", ["git config ", hooks_path, " scripts/hooks && git config --get ", hooks_path, " >", dev_null]))
	count(denials) == 0
}

test_allow_hooks_path_install_with_stderr_silenced if {
	denials := guard.deny with input as bash_event(concat("", ["git config ", hooks_path, " scripts/hooks 2>", dev_null]))
	count(denials) == 0
}

test_allow_hooks_path_read_redirected_to_dev_null if {
	denials := guard.deny with input as bash_event(concat("", ["git config --get ", hooks_path, " >", dev_null]))
	count(denials) == 0
}

test_allow_hooks_path_read_piped_to_tee_dev_null if {
	denials := guard.deny with input as bash_event(concat("", ["git config --get ", hooks_path, " | tee ", dev_null]))
	count(denials) == 0
}

# Two separate commands joined by the shim's `; `. A separator in the gap means
# the tokens belong to different commands, which is the whole distinction.
test_allow_hooks_path_repair_then_read_across_statements if {
	denials := guard.deny with input as bash_event(concat("", ["git config ", hooks_path, " scripts/hooks; git config --get ", hooks_path, " >", dev_null]))
	count(denials) == 0
}

test_allow_git_config_env_installing_hooks if {
	denials := guard.deny with input as bash_event(concat("", [
		"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=", hooks_path,
		" GIT_CONFIG_VALUE_0=scripts/hooks git status",
	]))
	count(denials) == 0
}

# hooksPath is set at index 0 and an UNRELATED key is discarded at index 1. The
# index capture is what keeps this out of the deny set.
test_allow_git_config_env_mismatched_indices if {
	denials := guard.deny with input as bash_event(concat("", [
		"GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=", hooks_path,
		" GIT_CONFIG_VALUE_0=scripts/hooks GIT_CONFIG_KEY_1=core.pager GIT_CONFIG_VALUE_1=",
		dev_null, " git status",
	]))
	count(denials) == 0
}
