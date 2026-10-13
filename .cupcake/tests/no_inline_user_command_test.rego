# OPA unit tests for no_inline_user_command. Run with:
#   opa test .cupcake/policies/claude/no_inline_user_command.rego \
#     .cupcake/tests/no_inline_user_command_test.rego
#
# The signal strings below are the shape `.cupcake/signals/last_assistant_inline_user_command.sh`
# emits; which closing messages produce them is proven in scripts/test-inline-user-command-signal.py,
# and the whole path through `cupcake eval` in scripts/test-cupcake-stop-guards.py.
package cupcake.policies.claude.no_inline_user_command_test

import rego.v1

import data.cupcake.policies.claude.no_inline_user_command as guard

bash_detail := "INLINEUSERCOMMAND:a shell-fenced block, the first fenced as bash, opening with: git push -u origin fix/x"

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_inline_user_command": sig},
}

rule_ids(halts) := {d.rule_id | some d in halts}

test_halt_on_tagged_signal if {
	halts := guard.halt with input as stop_event(bash_detail)
	"ER-EFFECTS-NO-INLINE-USER-COMMAND" in rule_ids(halts)
}

test_reason_quotes_detail_directive_and_helper if {
	halts := guard.halt with input as stop_event(bash_detail)
	some d in halts
	contains(d.reason, "git push -u origin fix/x")
	contains(d.reason, "you open a kitty tab for each inline bash block you want me to run")
	contains(d.reason, "python3 scripts/kitty-user-command.py --title '<short title>' --why '<why it should be run>' -- '<command>'")
	contains(d.reason, "names which tabs you opened")
}

test_halt_on_object_signal if {
	halts := guard.halt with input as {
		"hook_event_name": "Stop",
		"signals": {"last_assistant_inline_user_command": {"output": bash_detail, "exit_code": 0}},
	}
	"ER-EFFECTS-NO-INLINE-USER-COMMAND" in rule_ids(halts)
}

test_halt_on_untagged_nonempty_signal if {
	halts := guard.halt with input as stop_event("a shell-fenced block")
	"ER-EFFECTS-NO-INLINE-USER-COMMAND" in rule_ids(halts)
}

test_no_halt_on_empty_signal if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

test_no_halt_on_whitespace_signal if {
	halts := guard.halt with input as stop_event("  \n")
	count(halts) == 0
}

test_no_halt_on_missing_signal if {
	halts := guard.halt with input as {"hook_event_name": "Stop", "signals": {}}
	count(halts) == 0
}

test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "PreToolUse",
		"signals": {"last_assistant_inline_user_command": bash_detail},
	}
	count(halts) == 0
}
