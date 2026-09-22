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

rule_ids(denials) := {d.rule_id | some d in denials}

denied(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	RULE in rule_ids(denials)
}

denied_with_evidence(cmd, evidence) if {
	denials := guard.deny with input as bash_event_with_evidence(cmd, evidence)
	RULE in rule_ids(denials)
}

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
