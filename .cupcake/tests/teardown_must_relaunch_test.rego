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

rule_ids(denials) := {d.rule_id | some d in denials}

denied(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
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

# --- a teardown that is the whole command is ALLOWED --------------------------

# Ending a run on purpose. The sibling guard refuses a source edit while a run is
# live, so this has to be expressible or the two rules deadlock each other.
test_allow_bare_teardown_alone if {
	not denied("python3 scripts/er-teardown.py")
}

test_allow_teardown_alone_with_redirects if {
	not denied("python3 scripts/er-teardown.py > /dev/null 2>&1")
}

# `--reason` records why the run ended, in the run's own outcome record. It adds evidence and no
# work, so a deliberate ending may say what it was. Refusing it taught the agent to drop the flag
# to get past the guard, which cost the outcome line the only thing it had to say.
test_allow_teardown_alone_with_a_reason if {
	not denied("python3 scripts/er-teardown.py --reason band-tables-measured")
}

test_allow_teardown_alone_with_a_joined_reason if {
	not denied("python3 scripts/er-teardown.py --reason=band-tables-measured > /dev/null 2>&1")
}

# ...but the value is only excused as the flag's operand. A reason does not turn a chained command
# into a standalone teardown, and a bare word that is not a reason is still trailing work.
test_deny_teardown_with_a_reason_then_more_work if {
	denied("python3 scripts/er-teardown.py --reason measured; bash scripts/er-build-dlls.sh er-save-game-row")
}

# ...but riding along with other work is still the accident this rule exists for.
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
