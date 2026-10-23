# OPA unit tests for teardown_must_relaunch.
#
# Not loaded by the cupcake engine (which scans .cupcake/policies/<harness>/
# and .cupcake/system/ only). Run with:
#   opa test .cupcake/policies/claude/teardown_must_relaunch.rego \
#            .cupcake/tests/teardown_must_relaunch_test.rego
# End-to-end engine coverage lives in scripts/test-cupcake-policies.py.
package cupcake.policies.claude.teardown_must_relaunch_test

import rego.v1

import data.cupcake.policies.claude.teardown_must_relaunch as guard

RULE := "ER-EFFECTS-TEARDOWN-MUST-RELAUNCH"

bash_event(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "description": "test case"},
}

bash_event_with_evidence(cmd, evidence) := object.union(bash_event(cmd), {
	"signals": {"frida_evidence": evidence},
})

bash_event_with_stamp(cmd, stamp) := object.union(bash_event(cmd), {
	"signals": {"stamped_run": stamp},
})

rule_ids(denials) := {d.rule_id | some d in denials}

denied(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	RULE in rule_ids(denials)
}

denied_with_evidence(cmd, evidence) if {
	denials := guard.deny with input as bash_event_with_evidence(cmd, evidence)
	RULE in rule_ids(denials)
}

denied_with_stamp(cmd, stamp) if {
	denials := guard.deny with input as bash_event_with_stamp(cmd, stamp)
	RULE in rule_ids(denials)
}

PROVEN_STAMP := "PROVEN br-20261022-023259-d732 feat/asset-geometry-tooling a9be76256472 /home/banon/projects/er-mods-rs/.worktrees/feat-asset-geometry-tooling"

# --- a teardown with no relaunch is DENIED -----------------------------------

# The exact shape that killed run br-20260912-204637-08ba.
test_deny_teardown_stapled_to_a_build if {
	denied("python3 scripts/er-teardown.py > /dev/null 2>&1; bash scripts/er-build-dlls.sh er-save-game-row")
}

test_deny_teardown_with_absolute_path_and_trailing_work if {
	denied("python3 /home/x/repo/scripts/er-teardown.py && cargo build")
}

# Reporting what was torn down is not relaunching it.
test_deny_teardown_then_log_read if {
	denied("python3 scripts/er-teardown.py; tail -5 run.log")
}

# A dry run stages and launches nothing, so it does not discharge the rule.
test_deny_teardown_with_dry_run_launch if {
	denied("python3 scripts/er-teardown.py; python3 scripts/er-run-branch.py --dry-run --with er-save-game-row")
}

# --- a teardown that does not relaunch needs measurement evidence -------------

# The standalone form is not a deliberate-ending exception without a proof signal: it still leaves
# the user with no game.
test_deny_bare_teardown_alone_without_evidence if {
	denied("python3 scripts/er-teardown.py")
}

test_deny_teardown_alone_with_redirects_without_evidence if {
	denied("python3 scripts/er-teardown.py > /dev/null 2>&1")
}

# `--reason` records why a run ended, but it does not prove the run has paid for itself.
test_deny_teardown_alone_with_a_reason_without_evidence if {
	denied("python3 scripts/er-teardown.py --reason band-tables-measured")
}

test_deny_teardown_alone_with_a_joined_reason_without_evidence if {
	denied("python3 scripts/er-teardown.py --reason=band-tables-measured > /dev/null 2>&1")
}

# Exact takeover gap: a bare reasoned teardown is denied until the evidence signal is proven.
test_deny_takeover_gap_bare_reasoned_teardown_without_evidence if {
	denied("python3 scripts/er-teardown.py --reason append-line-handle-cache-edit")
}

# A proven measurement is the checkable condition for ending an agent-owned measurement run.
test_allow_teardown_alone_after_measurement if {
	not denied_with_evidence(
		"python3 scripts/er-teardown.py --reason band-tables-measured",
		"PROVEN telemetry crate=er-game-base line='measured'",
	)
}

test_allow_teardown_alone_with_joined_reason_after_measurement if {
	not denied_with_evidence(
		"python3 scripts/er-teardown.py --reason=band-tables-measured > /dev/null 2>&1",
		"PROVEN frida pid=123 messages=1",
	)
}

test_allow_teardown_alone_after_measurement_with_leading_cd if {
	not denied_with_evidence(
		"cd /home/banon/projects/er-mods-rs; python3 scripts/er-teardown.py --reason band-tables-measured",
		"PROVEN telemetry crate=er-title-flow line='measured'",
	)
}

test_allow_teardown_alone_after_measurement_with_hook_normalized_cd if {
	not denied_with_evidence(
		"cd /home/banon/projects/er-mods-rs python3 scripts/er-teardown.py --reason band-tables-measured",
		"PROVEN telemetry crate=er-title-flow line='measured'",
	)
}

# --- a standalone teardown that declares the run invalid is ALLOWED ------------
#
# AGENTS.md obliges the agent to stop an agent-launched run that has become invalid, and such a
# run has produced no measurement, so the evidence gate cannot be the condition. Refused
# 2026-10-07 on the build-import run whose import never applied.

test_allow_invalid_run_teardown_without_evidence if {
	not denied("python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=invalid-run-build-import-did-not-apply")
}

test_allow_invalid_run_teardown_with_output_trim if {
	not denied("python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=invalid-run-build-import-did-not-apply 2>&1 | tail -3")
}

test_allow_invalid_run_teardown_with_leading_cd if {
	not denied("cd /home/banon/projects/er-mods-rs; python3 scripts/er-teardown.py --reason=invalid-run-wrong-build > /dev/null 2>&1")
}

# The exact refused command, without the declaration, still needs evidence.
test_deny_undeclared_reason_without_evidence if {
	denied("python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=build-import-did-not-apply 2>&1 | tail -3")
}

test_deny_invalid_run_teardown_then_build if {
	denied("python3 scripts/er-teardown.py --reason=invalid-run-wrong-build; cargo build")
}

test_deny_invalid_run_teardown_and_then_build if {
	denied("python3 scripts/er-teardown.py --reason=invalid-run-wrong-build && bash scripts/er-build-dlls.sh er-save-game-row")
}

test_deny_invalid_run_teardown_piped_into_work if {
	denied("python3 scripts/er-teardown.py --reason=invalid-run-wrong-build | xargs cargo build")
}

test_deny_invalid_run_teardown_then_tail_of_a_file if {
	denied("python3 scripts/er-teardown.py --reason=invalid-run-wrong-build; tail -3 run.log")
}

test_deny_invalid_run_multi_word_reason if {
	denied(`python3 scripts/er-teardown.py --reason="invalid-run-import did not apply"`)
}

test_deny_invalid_run_space_separated_reason if {
	denied("python3 scripts/er-teardown.py --reason invalid-run-wrong-build")
}

test_deny_empty_invalid_run_token if {
	denied("python3 scripts/er-teardown.py --reason=invalid-run-")
}

# --- a standalone teardown that ends the run which stamped the push ------------
#
# AGENTS.md says to close Elden Ring before pushing, since check.sh refuses while it runs, and a
# push of crate code waits for a run of its exact tip. Refused 2026-10-22 after run
# br-20261022-023259-d732 had stamped a9be7625 and the user had authorized the push.

test_allow_stamped_teardown_after_stamping_run if {
	not denied_with_stamp(
		"python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=stamped-push-needs-game-closed",
		PROVEN_STAMP,
	)
}

test_allow_stamped_teardown_with_output_trim if {
	not denied_with_stamp(
		"python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=stamped-push-needs-game-closed 2>&1 | tail -3",
		PROVEN_STAMP,
	)
}

test_allow_stamped_teardown_with_leading_cd if {
	not denied_with_stamp(
		"cd /home/banon/projects/er-mods-rs; python3 scripts/er-teardown.py --reason=stamped-a9be7625 > /dev/null 2>&1",
		PROVEN_STAMP,
	)
}

# The declaration alone admits nothing: no signal, and a signal that could not prove the stamp,
# both leave it refused.
test_deny_stamped_teardown_without_signal if {
	denied("python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=stamped-push-needs-game-closed")
}

test_deny_stamped_teardown_with_unproven_signal if {
	denied_with_stamp(
		"python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=stamped-push-needs-game-closed",
		"UNPROVEN no-stamp br-20261022-023259-d732 has no log opening with a build line yet",
	)
}

# A signal that failed reaches the policy as a failure record, not a string.
test_deny_stamped_teardown_with_failed_signal if {
	denied_with_stamp(
		"python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=stamped-push-needs-game-closed",
		{"error": "", "exit_code": 1, "output": "PROVEN x", "success": false},
	)
}

# `PROVEN` has to open the answer; `UNPROVEN` contains it.
test_deny_stamped_teardown_with_proven_inside_the_answer if {
	denied_with_stamp(
		"python3 scripts/er-teardown.py --reason=stamped-x",
		"UNPROVEN no-evidence PROVEN",
	)
}

# The exact command refused on 2026-10-22 does not declare the form, so a proven stamp does not
# admit it.
test_deny_undeclared_reason_with_proven_stamp if {
	denied_with_stamp(
		"python3 scripts/er-teardown.py --reason=push-needs-game-closed",
		PROVEN_STAMP,
	)
}

test_deny_bare_teardown_with_proven_stamp if {
	denied_with_stamp("python3 scripts/er-teardown.py", PROVEN_STAMP)
}

test_deny_stamped_teardown_then_push if {
	denied_with_stamp(
		"python3 scripts/er-teardown.py --reason=stamped-x; git push -u origin feat/x",
		PROVEN_STAMP,
	)
}

test_deny_stamped_teardown_and_then_build if {
	denied_with_stamp(
		"python3 scripts/er-teardown.py --reason=stamped-x && bash scripts/er-build-dlls.sh er-save-game-row",
		PROVEN_STAMP,
	)
}

test_deny_stamped_teardown_then_tail_of_a_file if {
	denied_with_stamp("python3 scripts/er-teardown.py --reason=stamped-x; tail -3 run.log", PROVEN_STAMP)
}

test_deny_stamped_multi_word_reason if {
	denied_with_stamp(`python3 scripts/er-teardown.py --reason="stamped-push needs the game closed"`, PROVEN_STAMP)
}

test_deny_stamped_space_separated_reason if {
	denied_with_stamp("python3 scripts/er-teardown.py --reason stamped-x", PROVEN_STAMP)
}

test_deny_empty_stamped_token if {
	denied_with_stamp("python3 scripts/er-teardown.py --reason=stamped-", PROVEN_STAMP)
}

# The denial quotes what the signal answered, so the reason it could not prove the stamp is on
# screen.
test_denial_quotes_the_signal_answer if {
	denials := guard.deny with input as bash_event_with_stamp(
		"python3 scripts/er-teardown.py --reason=stamped-x",
		"UNPROVEN not-up br-1 launcher pid 7 is not alive",
	)
	some d in denials
	d.rule_id == RULE
	contains(d.reason, "`stamped_run` signal: UNPROVEN not-up br-1 launcher pid 7 is not alive")
	contains(d.reason, "--reason=stamped-<one-token-why>")
}

test_deny_teardown_with_a_reason_then_more_work if {
	denied("python3 scripts/er-teardown.py --reason measured; bash scripts/er-build-dlls.sh er-save-game-row")
}

test_deny_teardown_with_a_reason_then_more_work_even_after_measurement if {
	denied_with_evidence(
		"python3 scripts/er-teardown.py --reason measured; bash scripts/er-build-dlls.sh er-save-game-row",
		"PROVEN telemetry crate=er-game-base line='measured'",
	)
}

test_deny_teardown_then_second_command if {
	denied("python3 scripts/er-teardown.py > /dev/null 2>&1; bash scripts/er-build-dlls.sh er-save-game-row")
}

# --- a teardown that relaunches is ALLOWED -----------------------------------

test_allow_teardown_then_launch if {
	not denied("python3 scripts/er-teardown.py > /dev/null 2>&1; python3 scripts/er-run-branch.py --seed 1 --with er-save-game-row --without er-quickload")
}

test_allow_teardown_and_launch_with_absolute_paths if {
	not denied("python3 /home/x/repo/scripts/er-teardown.py; python3 /home/x/repo/scripts/er-run-branch.py --with er-save-game-row")
}

# The user's own launcher counts as a relaunch.
test_allow_teardown_then_user_launcher if {
	not denied("python3 scripts/er-teardown.py; bash /home/banon/Elden/launch.sh")
}

# A launcher behind a leading `NAME=value` is still the program the shell runs. The exact
# command refused on 2026-10-14: the `/` in the assigned path made the shared command-slot
# test read `ME3_PROFILE=/tmp/...` as the program and the launcher as its operand.
test_allow_teardown_then_env_prefixed_user_launcher if {
	not denied(`python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py > /dev/null 2>&1; ME3_PROFILE=/tmp/x/mushroom-man-crashlog.me3 /home/banon/Elden/launch.sh -o > /tmp/x/launch6.log 2>&1; echo "launcher returned exit=$? at $(date +%T)"`)
}

test_allow_teardown_then_env_prefixed_run_branch if {
	not denied("python3 scripts/er-teardown.py; ME3_PROFILE=/tmp/x/p.me3 python3 scripts/er-run-branch.py --with er-save-game-row")
}

test_allow_teardown_then_several_assignments_before_the_launcher if {
	not denied("python3 scripts/er-teardown.py; A=1 ME3_PROFILE=/tmp/x/p.me3 B_2=/y /home/banon/Elden/launch.sh")
}

test_allow_teardown_then_env_wrapper_with_path_assignment if {
	not denied("python3 scripts/er-teardown.py; env ME3_PROFILE=/tmp/x/p.me3 /home/banon/Elden/launch.sh")
}

# Refused on 2026-10-15 with the session's shell in `.worktrees/er-sky`. The global hook runs
# `cupcake eval` with no `--policy-dir`, so it judged that worktree's `.cupcake`, cut from an
# origin/main that still read a `/` in an assignment as the program. The profile path played no
# part: the vanilla-profile form below, allowed 40 minutes earlier, is denied by that rulebook
# too. It was allowed then because the shell sat in a directory with no `.cupcake` of its own.
test_allow_teardown_then_scratchpad_profile_user_launcher if {
	not denied(`python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py > /dev/null 2>&1; ME3_PROFILE=/tmp/claude-1000/-home-banon-projects-er-mods-rs/e7edaee5-d1da-4868-ab95-fa3613643f52/scratchpad/er-sky-sun.me3 /home/banon/Elden/launch.sh -o > /tmp/claude-1000/-home-banon-projects-er-mods-rs/e7edaee5-d1da-4868-ab95-fa3613643f52/scratchpad/er-sky-sun-launch.log 2>&1`)
}

test_allow_teardown_then_home_profile_user_launcher if {
	not denied(`python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py > /dev/null 2>&1; ME3_PROFILE=/home/banon/Elden/vanilla-nomods.me3 /home/banon/Elden/launch.sh -o > /tmp/claude-1000/-home-banon-projects-er-mods-rs/e7edaee5-d1da-4868-ab95-fa3613643f52/scratchpad/vanilla-rt-launch.log 2>&1`)
}

# An assignment in front of something that is not a launcher relaunches nothing.
test_deny_teardown_then_env_prefixed_non_launcher if {
	denied("python3 scripts/er-teardown.py; FOO=1 ls")
}

test_deny_teardown_then_path_assigned_non_launcher if {
	denied("python3 scripts/er-teardown.py; FOO=/x ls")
}

# A word with `=` whose left side is not a shell name is not an assignment: the shell runs
# that word as the program, so the launcher after it is an operand.
test_deny_teardown_then_non_name_assignment_before_launcher if {
	denied("python3 scripts/er-teardown.py; dir/a=b /home/banon/Elden/launch.sh")
}

# The teardown itself behind a path-valued assignment is still a teardown. Allowed before
# 2026-10-14, for the same reason the relaunch above was missed.
test_deny_path_assigned_teardown_then_build if {
	denied("FOO=/x python3 scripts/er-teardown.py; cargo build")
}

# Written across lines, which the live engine collapses before evaluation.
test_allow_multiline_teardown_and_launch if {
	not denied("python3 scripts/er-teardown.py \\\n  && python3 scripts/er-run-branch.py --with er-save-game-row")
}

# --- read-only status is always allowed --------------------------------------

test_allow_status if {
	not denied("python3 scripts/er-teardown.py --status")
}

test_allow_status_piped if {
	not denied("python3 scripts/er-teardown.py --status 2>&1 | tail -3")
}

# argparse prints usage and exits before any teardown code runs.
test_allow_help if {
	not denied("python3 scripts/er-teardown.py --help")
}

test_allow_short_help if {
	not denied("python3 scripts/er-teardown.py -h")
}

# The exact command refused on 2026-10-05: reads chained with a usage read.
test_allow_help_chained_with_reads if {
	not denied(`ls -la /x/er_allow_hostile_actions.dll 2>&1; grep -nE "ME3_PROFILE|profile" /home/banon/Elden/launch.sh | head -12; cd /home/banon/projects/er-mods-rs && $HOME/.local/bin/bd memories "lab launch" 2>/dev/null | head -5; python3 scripts/er-teardown.py --help 2>&1 | head -15`)
}

# A help read earlier in the command does not excuse a real kill later in it.
test_deny_help_then_a_real_teardown if {
	denied("python3 scripts/er-teardown.py --help; python3 scripts/er-teardown.py; cargo build")
}

test_deny_short_help_then_a_real_teardown if {
	denied("python3 scripts/er-teardown.py -h; python3 scripts/er-teardown.py; cargo build")
}

# --- the guard stays out of everything else ----------------------------------

# A similarly-named script is not the teardown.
test_allow_lookalike_script if {
	not denied("python3 scripts/er-teardown-report.py")
}

test_allow_unrelated_command if {
	not denied("bash scripts/er-build-dlls.sh er-save-game-row")
}

test_allow_read_only_python_extraction_without_teardown if {
	not denied(`python3 - <<'PY'
from pathlib import Path
ranges = [
 ('driver.rs', Path('crates/er-npc-possess/src/possess/driver.rs'), [(240,310),(1125,1245),(1645,1815)]),
 ('game.rs', Path('crates/er-npc-possess/src/possess/game.rs'), [(760,845),(1088,1125),(1228,1255),(1436,1504)]),
 ('netdamage.rs', Path('crates/er-npc-possess/src/possess/netdamage.rs'), [(1,118),(330,445),(470,560)]),
 ('layout.rs', Path('crates/er-npc-possess/src/possess/layout.rs'), [(68,140),(470,505),(1160,1195),(1218,1260)]),
]
for title,path,spans in ranges:
 print('\\n##', title)
 lines=path.read_text().splitlines()
 for a,b in spans:
  print(f'-- {a}-{b}')
  for i in range(a,min(b,len(lines))+1):
   s=lines[i-1]
   if any(tok in s for tok in ['INVINC','teamType','request_move','SetHP','Packet15','Receive','set_invincible','set_alpha','set_no_attack','set_body_scale','set_camera_override','players_in_world','last_received_damage_packet','packet15_receive','TEAM_TYPE','TINT_ALPHA','SCALE_SIZE','camOverride','remote player','mode','Incoming','PvP DAMAGE','request_move','body is stretched','hurtbox','collision','PlayerIns','ChrIns','co-locate','camera', 'Charmed', 'SetStamina']):
    print(f'{i}: {s}')
PY`)
}

test_allow_other_events if {
	denials := guard.deny with input as {
		"hook_event_name": "PostToolUse",
		"tool_name": "Bash",
		"tool_input": {"command": "python3 scripts/er-teardown.py"},
	}
	not RULE in rule_ids(denials)
}

# --- a payload that NAMES the script is not an invocation of it ---------------
#
# Both shapes below were refused in production on 2026-09-21 (bd er-effects-rs-ak3q,
# bd er-effects-rs-if5l). The invoked binary is `git` and `bd`; the script appears in a
# commit message and an issue body, where it is prose. The cost was not the block: the
# commit message had to be reworded to say "a repo-relative teardown command" rather than
# name the file, and the issue reporting it had to be filed through a body file written by
# a separate command, so the record this guard is not there to police came out degraded.

test_allow_commit_message_naming_the_script if {
	not denied(`git commit -m "fix(guard): teardown_must_relaunch fires on scripts/er-teardown.py in prose"`)
}

test_allow_commit_heredoc_naming_the_script if {
	not denied(`git commit -F - <<'EOF'
fix(guard): decide on the command, not the text

scripts/er-teardown.py named in a commit message is documentation, and
python3 scripts/er-teardown.py; cargo build in one is still documentation.
EOF`)
}

test_allow_issue_body_naming_the_script if {
	not denied(`bd create --description "scripts/er-teardown.py must be paired with a launch; see python3 scripts/er-teardown.py; bash scripts/er-build-dlls.sh"`)
}

test_allow_memory_body_naming_the_script if {
	not denied(`bd remember --key teardown-guard-note "the shape that was refused was python3 scripts/er-teardown.py > /dev/null 2>&1; bash scripts/er-build-dlls.sh er-save-game-row"`)
}

# ...and the guard still reaches a real invocation inside a shell wrapper payload, which
# is the half a naive "is the first word git" test would lose.
test_deny_teardown_inside_a_shell_wrapper_payload if {
	denied(`bash -c 'python3 scripts/er-teardown.py; cargo build'`)
}

# A `--status` earlier in the command does not excuse a real kill later in it. The
# two-rule shape this replaced asked only whether SOME teardown was followed by
# `--status`, so this chained past it.
test_deny_status_then_a_real_teardown if {
	denied("python3 scripts/er-teardown.py --status; python3 scripts/er-teardown.py; cargo build")
}

# --- a standalone teardown the user's push authorization pays for -------------
#
# User directive 2026-10-22: "to authorize a push sha is to authorize a teardown". Refused that
# day as `--reason=cursor-test-done-for-push` while the user's `authorize 58a59772...` waited on a
# game that stamped nothing, so the `stamped-` form could not admit it.

bash_event_with_push_auth(cmd, answer) := object.union(bash_event(cmd), {
	"signals": {"push_teardown": answer},
})

denied_with_push_auth(cmd, answer) if {
	denials := guard.deny with input as bash_event_with_push_auth(cmd, answer)
	RULE in rule_ids(denials)
}

AUTHORIZED_PUSH := "AUTHORIZED\tfix/me3-launch-binaries-dir\t58a59772dbd2b83a271cee9bc593b26a79aad429\t0b9f3c1e-0000-4000-8000-000000000000"

test_allow_push_teardown_after_authorization if {
	not denied_with_push_auth(
		"python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=push-me3-guard",
		AUTHORIZED_PUSH,
	)
}

test_allow_push_teardown_with_output_trim if {
	not denied_with_push_auth(
		"python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=push-me3-guard | tail -2",
		AUTHORIZED_PUSH,
	)
}

# The declaration alone admits nothing.
test_deny_push_teardown_without_signal if {
	denied("python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=push-me3-guard")
}

test_deny_push_teardown_with_denied_signal if {
	denied_with_push_auth(
		"python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=push-me3-guard",
		"DENIED\tthe user's last message is not `authorize <sha>`",
	)
}

test_deny_push_teardown_with_failed_signal if {
	denied_with_push_auth(
		"python3 scripts/er-teardown.py --reason=push-x",
		{"error": "", "exit_code": 1, "output": "AUTHORIZED\tx\ty\tz", "success": false},
	)
}

# An authorization pays for the push form only, never for another reason.
test_deny_other_reason_with_authorized_push if {
	denied_with_push_auth("python3 scripts/er-teardown.py --reason=done-testing", AUTHORIZED_PUSH)
}

test_deny_bare_teardown_with_authorized_push if {
	denied_with_push_auth("python3 scripts/er-teardown.py", AUTHORIZED_PUSH)
}

# Anything chained after the teardown is refused; the push runs as its own command.
test_deny_push_teardown_then_more_work if {
	denied_with_push_auth("python3 scripts/er-teardown.py --reason=push-x; git status", AUTHORIZED_PUSH)
}

test_deny_push_teardown_and_then_build if {
	denied_with_push_auth(
		"python3 scripts/er-teardown.py --reason=push-x && bash scripts/er-build-dlls.sh er-hook",
		AUTHORIZED_PUSH,
	)
}

test_deny_push_multi_word_reason if {
	denied_with_push_auth(`python3 scripts/er-teardown.py --reason="push the guard"`, AUTHORIZED_PUSH)
}
