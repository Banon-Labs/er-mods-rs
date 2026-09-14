# OPA unit tests for git_require_runtime_evidence.
# Run with:
#   opa test .cupcake/system/commands.rego \
#     .cupcake/policies/claude/git_require_runtime_evidence.rego \
#     .cupcake/tests/git_require_runtime_evidence_test.rego
package cupcake.policies.claude.git_require_runtime_evidence_test

import rego.v1

import data.cupcake.policies.claude.git_require_runtime_evidence as guard

RULE := "ER-EFFECTS-REQUIRE-RUNTIME-EVIDENCE"

# The signal is a bare verdict word; the prose lives in the sibling `runtime_evidence_note` signal
# and is never branched on. See the policy header for why it is not one parsed line.
MISSING := "MISSING"

OK := "OK"

NOTRUNTIME := "NOTRUNTIME"

UNKNOWN := "UNKNOWN"

NOTE := "er-quickload-autoload-debug.log was built from b6b45956, not 0e084240"

bash_event(cmd, evidence) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "timeout": 30000},
	"signals": {"runtime_evidence_for_head": evidence, "runtime_evidence_note": NOTE},
}

# A signal that exits non-zero does not reach a policy as its output. Cupcake replaces the string
# with a record of the failure -- measured 2026-09-13 in the debug capture of a live refusal:
#
#     {"error": "", "exit_code": 1, "output": "...", "success": false}
#
# This helper builds that shape so the fail-open can be asserted rather than assumed. It used to
# be a second copy of `bash_event` under a name promising the object, so the one test that called
# it proved nothing the first helper had not already proved.
bash_event_object_signal(cmd, evidence) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "timeout": 30000},
	"signals": {
		"runtime_evidence_for_head": {
			"error": "",
			"exit_code": 1,
			"output": evidence,
			"success": false,
		},
		"runtime_evidence_note": NOTE,
	},
}

bash_event_object_note(cmd, evidence) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "timeout": 30000},
	"signals": {
		"runtime_evidence_for_head": evidence,
		"runtime_evidence_note": {
			"error": "",
			"exit_code": 1,
			"output": NOTE,
			"success": false,
		},
	},
}

bash_event_no_signal(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "timeout": 30000},
	"signals": {},
}

rule_ids(denials) := {d.rule_id | some d in denials}

# --- the case this exists for -------------------------------------------------

test_deny_push_when_the_running_build_is_not_the_tip if {
	denials := guard.deny with input as bash_event("git push -u origin feat/x", MISSING)
	RULE in rule_ids(denials)
}

# --- the command names a different checkout than the caller's ------------------
#
# The verdict describes THE REPOSITORY BEING PUSHED, not the directory the signal process started
# in, and `scripts/cupcake_push_target_repo.py` is what makes that true. The distinction was not
# free: on 2026-09-13 a session working in the main checkout ran `cd <other worktree> && git push`
# and this rule refused it over the main checkout's tip, a commit the push did not contain. The
# same mix-up is what let a push of unproven game code through whenever the session's own directory
# happened to have evidence.
#
# So a redirected push is not a special case here, and that is the point: once the signal measures
# the right repository, `MISSING` means the same thing whatever directory the command names.

test_deny_a_push_redirected_to_a_checkout_with_no_evidence if {
	denials := guard.deny with input as bash_event("cd /other/worktree && git push -u origin HEAD", MISSING)
	RULE in rule_ids(denials)
}

test_deny_a_git_c_push_at_a_checkout_with_no_evidence if {
	denials := guard.deny with input as bash_event("git -C /other/worktree push origin main", MISSING)
	RULE in rule_ids(denials)
}

# The answer the resolver gives when a redirect is present and it cannot say what the redirect
# means -- a subshell, a heredoc, quoting that will not lex, a directory that is not a working tree
# of this repository, or two pushes aimed at two checkouts. Measuring the caller's own directory
# instead is precisely how the wrong repository came to be judged, so the signal says `UNKNOWN` and
# this rule stands down. The pre-push hook still measures that push exactly, from the directory git
# hands it.
test_allow_a_redirected_push_the_signal_could_not_resolve if {
	denials := guard.deny with input as bash_event("(cd /other/worktree && git push)", UNKNOWN)
	not RULE in rule_ids(denials)
}

# --- a signal that failed is not a verdict ------------------------------------

# Cupcake hands a failed signal to the policy as a record of the failure rather than as its output.
# For the verdict that has to mean no denial: an object is not the word `MISSING`, the comparison
# is undefined, and the default `UNKNOWN` takes over. A guard that cannot see must not invent one.
test_allow_when_the_verdict_signal_failed if {
	denials := guard.deny with input as bash_event_object_signal("git push", MISSING)
	not RULE in rule_ids(denials)
}

# For the prose the fail-open is different: the verdict still stands, and the refusal loses only
# its sentence. That is worth denying on -- but it is also worth noticing, because it happened.
# `.cupcake/signals/runtime_evidence_note.sh` used to exit 1 whenever no log named the tip, which
# is the one verdict that reads the note, so a live refusal on 2026-09-13 said "no measurement was
# available" while the measurement sat inside the discarded object. The signal now exits 0; this
# keeps the fallback honest if anything else ever fails there.
test_deny_without_the_sentence_when_the_note_signal_failed if {
	denials := guard.deny with input as bash_event_object_note("git push", MISSING)
	RULE in rule_ids(denials)
	some d in denials
	contains(d.reason, "no measurement was available")
}

# The note must reach the user intact: "no evidence" without "and here is what ran instead" is not
# actionable.
test_the_denial_names_what_actually_ran if {
	denials := guard.deny with input as bash_event("git push", MISSING)
	some d in denials
	contains(d.reason, "was built from b6b45956, not 0e084240")
}

test_deny_a_push_hidden_in_a_shell_wrapper if {
	denials := guard.deny with input as bash_event("bash -c 'git push -u origin feat/x'", MISSING)
	RULE in rule_ids(denials)
}

test_deny_a_push_after_another_command if {
	denials := guard.deny with input as bash_event("cargo fmt && git push", MISSING)
	RULE in rule_ids(denials)
}

test_deny_git_c_push if {
	denials := guard.deny with input as bash_event("git -C /tmp/repo push", MISSING)
	RULE in rule_ids(denials)
}

# --- the cases it must leave alone -------------------------------------------

test_allow_push_when_the_tip_is_what_ran if {
	denials := guard.deny with input as bash_event("git push -u origin feat/x", OK)
	not RULE in rule_ids(denials)
}

# A docs or policy push has nothing for a run to prove. A guard that fires on everything is one the
# next agent overrides by reflex, which is worse than not having it.
test_allow_push_when_no_crate_changed if {
	denials := guard.deny with input as bash_event("git push", NOTRUNTIME)
	not RULE in rule_ids(denials)
}

# UNKNOWN is not MISSING. A guard that cannot measure must not invent a verdict.
test_allow_push_when_the_signal_could_not_measure if {
	denials := guard.deny with input as bash_event("git push", UNKNOWN)
	not RULE in rule_ids(denials)
}

test_allow_push_when_the_signal_is_absent if {
	denials := guard.deny with input as bash_event_no_signal("git push")
	not RULE in rule_ids(denials)
}

# Anything that is not the exact word MISSING must allow, including a verdict this policy has
# never heard of. Fail-open on an unrecognised word is the same rule as UNKNOWN above.
test_allow_push_when_the_verdict_is_unrecognised if {
	denials := guard.deny with input as bash_event("git push", "something else entirely")
	not RULE in rule_ids(denials)
}

# Only a push. This guard has no opinion about any other git command, and saying so is what stops
# it growing into a general "did you test it" nag on commands that publish nothing.
test_allow_a_commit_with_no_evidence if {
	denials := guard.deny with input as bash_event("git commit -m x", MISSING)
	not RULE in rule_ids(denials)
}

test_allow_a_fetch_with_no_evidence if {
	denials := guard.deny with input as bash_event("git fetch origin", MISSING)
	not RULE in rule_ids(denials)
}

test_allow_a_status_with_no_evidence if {
	denials := guard.deny with input as bash_event("git status --short", MISSING)
	not RULE in rule_ids(denials)
}

# The word "push" in prose, or a path that contains it, is not a push. This is the false positive
# that made the sibling main-push guard route through commands.executed_texts.
test_allow_prose_mentioning_a_push if {
	denials := guard.deny with input as bash_event("echo 'remember to git push later'", MISSING)
	not RULE in rule_ids(denials)
}

test_allow_a_non_git_command_named_push if {
	denials := guard.deny with input as bash_event("./scripts/push-notes.sh", MISSING)
	not RULE in rule_ids(denials)
}

# --- non-vacuity --------------------------------------------------------------

# Every allow-case above passes trivially if the deny rule never fires at all. This is the control
# that proves the suite is measuring something: the same command, the same guard, one field
# different, and it must go red.
test_the_allow_cases_are_not_vacuous if {
	allowed := guard.deny with input as bash_event("git push -u origin feat/x", OK)
	denied := guard.deny with input as bash_event("git push -u origin feat/x", MISSING)
	not RULE in rule_ids(allowed)
	RULE in rule_ids(denied)
}
