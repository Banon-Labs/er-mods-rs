# METADATA
# scope: package
# title: Git Block No-Verify - Builtin Policy
# authors: ["Cupcake Builtins"]
# custom:
#   severity: HIGH
#   id: BUILTIN-GIT-BLOCK-NO-VERIFY
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.builtins.git_block_no_verify

import rego.v1

import data.cupcake.system.commands

# Shell-wrapper decomposition (2026-08-26, bd er-effects-rs-dt2e). This policy
# recognises its verbs through commands.has_verb, whose `(^|\s)verb(\s|$)`
# boundary a quote does not satisfy -- so a hook bypass wrapped as
# `bash -c 'git commit --no-verify -m x'` produced ZERO denials, measured.
#
# The scan set below is STRICTLY ADDITIVE on purpose: it is the executed-text
# decomposition (which makes wrapper payloads visible) UNION the raw command
# exactly as this policy saw it before. Nothing this builtin used to deny can
# stop being denied. That is deliberately narrower than the repo-owned git
# guards, which also had their quoted-prose false positives fixed: this file is
# a vendored Cupcake builtin, and its own false-positive surface -- an unanchored
# `contains(cmd, "--no-verify")` that fires on a doc, commit message or heredoc
# merely NAMING the flag -- is a separate defect, filed rather than changed here.
no_verify_scan_texts := commands.executed_texts(input.tool_input.command) | {input.tool_input.command}

# Block git commands that bypass verification hooks
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"

	# Every text this command actually executes, plus the raw command itself
	some text in no_verify_scan_texts
	command := lower(text)

	# Check if it's a git command with --no-verify flag
	contains_git_no_verify(command)

	decision := {
		"rule_id": "BUILTIN-GIT-BLOCK-NO-VERIFY",
		"reason": "Git operations with --no-verify are not permitted. Commit hooks must run for code quality and security checks.",
		"severity": "HIGH",
	}
}

# Check if command contains git with --no-verify flag
# Uses helper library to prevent spacing bypass (TOB-EQTY-LAB-CUPCAKE-3)
contains_git_no_verify(cmd) if {
	# Check for git commit with --no-verify
	commands.has_verb(cmd, "git")
	commands.has_verb(cmd, "commit")
	contains(cmd, "--no-verify")
}

contains_git_no_verify(cmd) if {
	# Check for git commit with -n (shorthand for --no-verify), but only
	# inside the actual git-commit command segment. A later command like
	# `| sed -n ...` must not make `git commit -h | sed -n` look like a
	# no-verify commit.
	regex.match(git_commit_short_no_verify_pattern, cmd)
}

git_commit_short_no_verify_pattern := `(?m)(^|[;&|]\s*|\n)\s*git(\s+(?:-C|-c)\s+\S+|\s+--git-dir=\S+|\s+--work-tree=\S+|\s+--namespace=\S+)*\s+commit(\s+[^;&|\n]*)?\s+-[a-z]*n[a-z]*(\s|$|[;&|])`

contains_git_no_verify(cmd) if {
	# Check for git push with --no-verify
	commands.has_verb(cmd, "git")
	commands.has_verb(cmd, "push")
	contains(cmd, "--no-verify")
}

contains_git_no_verify(cmd) if {
	# Check for git merge with --no-verify
	commands.has_verb(cmd, "git")
	commands.has_verb(cmd, "merge")
	contains(cmd, "--no-verify")
}

# Also block attempts to disable hooks via config
deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"

	some text in no_verify_scan_texts
	command := lower(text)

	# Check if trying to disable hooks via git config
	contains_hook_disable(command)

	decision := {
		"rule_id": "BUILTIN-GIT-BLOCK-NO-VERIFY",
		"reason": "Disabling git hooks is not permitted. Hooks are required for code quality and security.",
		"severity": "HIGH",
	}
}

# ---------------------------------------------------------------------------
# core.hooksPath (2026-08-31, bd committed-state-compile-gate-2026-08-31)
#
# This rule used to AND `contains(cmd, "core.hookspath")` with
# `contains(cmd, "/dev/null")` over the WHOLE command string, so the two only had
# to be CO-PRESENT, not related. That denied
#
#     git config core.hooksPath scripts/hooks && git config --get core.hooksPath >/dev/null
#
# -- a command that INSTALLS hooks and merely silences an unrelated read -- with
# "Disabling git hooks is not permitted". Measured live, and it fired on an agent
# REPAIRING core.hooksPath after the er-effects-rs -> er-mods-rs rename left it
# pointing at an absolute path that no longer existed, so git had silently run NO
# hooks since 39a919e0. A guard that blocks the repair of its own total failure is
# worse than a guard that is merely noisy.
#
# The fix is not to drop the `/dev/null` half -- `git config core.hooksPath
# /dev/null` is a real way to disable hooks and must stay denied. It is to require
# the two to be THE SAME ASSIGNMENT: only whitespace, an `=`, quotes, or a
# line-continuation backslash may stand between the key and the value. A `>`, a
# `2>`, a `|` or a `;` in that gap means they belong to different commands, which
# is exactly what the false positive was.
#
# WHY THOSE GAP CHARACTERS AND NO OTHERS, against the shape the engine actually
# delivers (bd cupcake-tests-vs-delivered-input-shape-and-wrapper-payload-hole):
#   * space/tab -- `git config core.hooksPath /dev/null`, and the engine's
#     whitespace_normalization has already collapsed any run of them to one;
#   * `=` -- the `git -c core.hooksPath=/dev/null <cmd>` form, which the old rule
#     ALLOWED outright because it carries no `config` verb (measured);
#   * quotes -- `core.hooksPath "/dev/null"`; commands.scan_text blanks a quoted
#     span's ANCHORS but leaves the quote characters themselves in place;
#   * backslash + newline -- a continued command line, `git config core.hooksPath \`
#     newline `/dev/null`, which the shim leaves as a newline (it is a join, not a
#     boundary) and the engine then collapses to `\ `.
#
# The `git` requirement is kept, loosened to admit `/usr/bin/git`: it is what stops
# prose that merely NAMES the setting from being read as an attempt to apply it.
# The trailing-slash form is written the same way as commands.shell_name_pattern so
# a word merely ENDING in git (`legit`) cannot satisfy it.
hooks_path_dev_null_pattern := `core\.hookspath[ \t\r\n\\'"]*=?[ \t\r\n\\'"]*/dev/null`

git_invocation_pattern := `(^|[ \t;&|(){}])([^ \t;&|(){}'"]*/)?git([ \t]|$)`

contains_hook_disable(cmd) if {
	regex.match(git_invocation_pattern, cmd)

	# Lowercased: every caller passes `lower(text)`, so the shipped
	# `core.hooksPath` spelling could never match and this rule was dead.
	regex.match(hooks_path_dev_null_pattern, cmd)
}

# The GIT_CONFIG_* environment form is the documented way to set a config value
# without `git config` and without `-c`, and nothing here reasoned about it before:
#
#     GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit
#
# Its key and value live in SEPARATE assignments, so no same-assignment pattern can
# reach it; it needs its own clause. The captured index must MATCH on both sides, so
# a command that legitimately sets core.hooksPath at index 0 and some unrelated key
# to /dev/null at index 1 is not swept up. No `git` requirement here: the variable
# names already say git, and requiring the verb would miss `/usr/bin/git`.
git_config_env_key_pattern := `git_config_key_([0-9]+)[ \t]*=[ \t]*['"]*core\.hookspath`

git_config_env_value_pattern := `git_config_value_([0-9]+)[ \t]*=[ \t]*['"]*/dev/null`

contains_hook_disable(cmd) if {
	some key_match in regex.find_all_string_submatch_n(git_config_env_key_pattern, cmd, -1)
	some value_match in regex.find_all_string_submatch_n(git_config_env_value_pattern, cmd, -1)
	key_match[1] == value_match[1]
}

contains_hook_disable(cmd) if {
	# Detect attempts to chmod hooks to non-executable
	commands.has_verb(cmd, "chmod")
	regex.match(`\.git/hooks`, cmd)
	regex.match(`-x|-[0-9]*0[0-9]*`, cmd) # Removing execute permission
}

contains_hook_disable(cmd) if {
	# Detect attempts to remove hook files
	contains(cmd, ".git/hooks")
	removal_cmds := {"rm", "unlink", "trash"}
	commands.has_dangerous_verb(cmd, removal_cmds)
}

contains_hook_disable(cmd) if {
	# Detect moving/renaming hooks to disable them
	commands.has_verb(cmd, "mv")
	contains(cmd, ".git/hooks")
}
