# OPA unit tests for gh_pr_title_conventional.
# Run with:
#   opa test .cupcake/policies/claude/gh_pr_title_conventional.rego \
#     .cupcake/tests/gh_pr_title_conventional_test.rego
package cupcake.policies.claude.gh_pr_title_conventional_test

import rego.v1

import data.cupcake.policies.claude.gh_pr_title_conventional as guard

RULE := "ER-EFFECTS-PR-TITLE-CONVENTIONAL"

bash_event(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd},
}

rule_ids(denials) := {d.rule_id | some d in denials}

denied(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	RULE in rule_ids(denials)
}

# --- the case this exists for -------------------------------------------------

# The exact title CI refused on 2026-09-09.
test_deny_the_title_ci_actually_refused if {
	denied(`gh pr create --draft --base main --title "Cancel a local invasion that lands in the wrong place" --body-file /tmp/b.md`)
}

test_deny_on_edit_too if {
	denied(`gh pr edit 421 --title "make the thing work"`)
}

test_deny_a_single_quoted_title if {
	denied(`gh pr create --title 'no type here'`)
}

test_deny_a_bare_title if {
	denied("gh pr create --title untyped")
}

# A type that is not on the list is not a type. `feature:` is the near miss worth naming.
test_deny_a_near_miss_type if {
	denied(`gh pr create --title "feature(x): close but not a listed type"`)
}

# A colon alone is not the shape either.
test_deny_a_colon_without_a_type if {
	denied(`gh pr create --title "invasion warp: cancel a local invasion"`)
}

test_deny_a_type_with_no_description if {
	denied(`gh pr create --title "feat:"`)
}

# --- what it must accept ------------------------------------------------------

test_allow_a_plain_type if {
	not denied(`gh pr create --title "feat: cancel a local invasion that lands in the wrong place"`)
}

test_allow_a_scope if {
	not denied(`gh pr create --title "fix(er-invasion-warp): re-pin the ersc.dll entry points"`)
}

test_allow_a_breaking_marker if {
	not denied(`gh pr create --title "chore!: drop the 1.16.2 address table"`)
}

test_allow_a_scoped_breaking_marker if {
	not denied(`gh pr create --title "feat(invasion-warp)!: change the config shape"`)
}

# --- what it must not reach ---------------------------------------------------

test_allow_a_pr_command_with_no_title if {
	not denied("gh pr create --draft --fill")
}

test_allow_a_pr_view if {
	not denied(`gh pr view 421 --json title`)
}

test_allow_a_pr_checks_read if {
	not denied("gh pr checks 421")
}

# A commit subject is a different gate's business; this one is only about PR titles.
test_allow_a_git_commit_with_a_bare_subject if {
	not denied(`git commit -m "no type here"`)
}

# The word appearing in prose is not the command.
test_allow_prose_mentioning_it if {
	not denied(`echo "remember to gh pr create later"`)
}

# --- whose title it is --------------------------------------------------------
#
# 2026-10-13: the kitty hand-off below was refused naming `push python-write guard fix`, the
# helper's own tab title. Only the title flags of the `gh pr create` / `gh pr edit` invocation
# itself are judged -- including when that invocation is one quoted argument to another program.

reasons(cmd) := {d.reason | some d in guard.deny with input as bash_event(cmd)}

kitty_handoff(gh_title) := concat("", [
	`cd /home/banon/projects/er-mods-rs/.worktrees/pr-kitty-tabs && python3 scripts/kitty-user-command.py `,
	`--title 'push python-write guard fix' --why 'The agent may not push. ...' -- `,
	`'git -C /home/banon/projects/er-mods-rs push -u origin fix/x && gh pr create -R Banon-Labs/er-mods-rs `,
	`--draft --base main --head fix/x --title "`, gh_title, `" --body-file /tmp/pr.md'`,
])

# The exact command refused on 2026-10-13.
test_allow_the_kitty_handoff_that_was_refused if {
	cmd := `cd /home/banon/projects/er-mods-rs/.worktrees/pr-kitty-tabs && python3 scripts/kitty-user-command.py --title 'push python-write guard fix' --why 'The agent may not push. ...' -- 'git -C /home/banon/projects/er-mods-rs push -u origin fix/x && gh pr create -R Banon-Labs/er-mods-rs --draft --base main --head fix/x --title "fix(cupcake): admit committed scripts in .worktrees checkouts; close six scratch-script escapes" --body-file /tmp/pr.md'`
	cmd == kitty_handoff("fix(cupcake): admit committed scripts in .worktrees checkouts; close six scratch-script escapes")
	not denied(cmd)
}

# The same hand-off carrying a bad PR title is still refused, and the refusal names the PR
# title rather than the tab title.
test_deny_a_bad_title_inside_the_kitty_handoff if {
	cmd := kitty_handoff("push it")
	denied(cmd)
	some r in reasons(cmd)
	contains(r, "title : push it\n")
	not contains(r, "push python-write guard fix")
}

test_deny_a_plain_bad_title if {
	denied(`gh pr create --title "bad"`)
}

test_allow_a_wrapper_title_when_gh_sets_none if {
	not denied(`python3 scripts/kitty-user-command.py --title 'push it' -- 'gh pr create --draft --fill'`)
}

test_allow_another_programs_title_before_gh if {
	not denied(`python3 tool.py --title 'not typed' && gh pr create --title "feat: typed"`)
}

test_allow_another_programs_title_after_gh if {
	not denied(`gh pr create --fill; tool --title 'not typed'`)
}

# The `;` is inside the quoted title, so it does not end the command.
test_allow_a_separator_inside_the_title if {
	not denied(`gh pr create --title "fix: a; b" --body-file /tmp/b.md`)
}

test_deny_the_short_flag if {
	denied(`gh pr create -t 'no type here'`)
}

test_deny_the_equals_form if {
	denied(`gh pr create --title="no type here"`)
}

test_allow_the_equals_form if {
	not denied(`gh pr create --title="feat: typed" --draft`)
}

test_deny_the_short_equals_form if {
	denied(`gh pr create -t=untyped`)
}

test_deny_a_single_quoted_title_in_a_double_quoted_wrapper if {
	denied(`bash -c "gh pr create --title 'no type here'"`)
}

test_deny_an_escaped_title_in_a_double_quoted_wrapper if {
	denied(`bash -c "gh pr create --title \"no type here\""`)
}

test_allow_an_escaped_title_in_a_double_quoted_wrapper if {
	not denied(`bash -c "gh pr create --title \"feat: typed\" --draft"`)
}

test_deny_two_levels_of_nesting if {
	denied(`bash -c "ssh host 'gh pr create --title untyped'"`)
}

# gh keeps the last of a repeated flag, so that is the one judged.
test_the_last_repeated_title_is_judged if {
	not denied(`gh pr create --title "no type" --title "feat: typed"`)
	denied(`gh pr create --title "feat: typed" --title "no type"`)
}

test_deny_across_a_line_continuation if {
	denied("gh pr create --draft \\\n  --title 'no type here'")
}

test_deny_the_second_of_two_invocations if {
	denied(`gh pr create --title "feat: typed" && gh pr edit 1 --title "no type"`)
}

test_deny_a_gh_named_by_path if {
	denied(`/usr/bin/gh pr create --title "no type"`)
}

# A body that quotes an example command is an argument of gh, which never runs it.
test_allow_a_body_quoting_an_untyped_example if {
	not denied(`gh pr create --draft --title "fix: read the right title" --body "refused: gh pr create --title 'push it'"`)
}

# The same text as another program's argument does run, so it is still judged.
test_deny_the_example_when_another_program_runs_it if {
	denied(`gh pr create --draft --title "fix: read the right title" && bash -c "gh pr create --title 'push it'"`)
}

# Two different bad titles yield two decisions, not a rule conflict.
test_two_bad_titles_are_both_reported if {
	cmd := `gh pr create --title "first bad"; gh pr edit 2 --title "second bad"`
	count(reasons(cmd)) == 2
}

# --- non-vacuity --------------------------------------------------------------

test_the_allow_cases_are_not_vacuous if {
	not denied(`gh pr create --title "feat: a typed subject"`)
	denied(`gh pr create --title "an untyped subject"`)
}
