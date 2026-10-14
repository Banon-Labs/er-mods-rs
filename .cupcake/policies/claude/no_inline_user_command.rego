# METADATA
# scope: package
# title: Ban handing the user a command as an inline shell block instead of a kitty tab
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-INLINE-USER-COMMAND
#   description: >-
#     User directive 2026-10-13: "Update your standing order so that instead of providing me
#     inline bash, you open a kitty tab for each inline bash block you want me to run,
#     pre-pasted, with a comment block at the top saying why it should be ran".
#
#     The signal fires when the closing prose carries a fenced code block whose info string is a
#     shell language (bash, sh, shell, zsh, fish, console, shell-session and their kin,
#     case-insensitive). Other fence languages, bare fences and inline code do not count. The
#     replacement is scripts/kitty-user-command.py, which types each command into its own focused
#     kitty tab, unsubmitted, under a comment block saying why. The halt names what was found and
#     quotes the command line to do instead.
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_inline_user_command"]
package cupcake.policies.claude.no_inline_user_command

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	some d in [detail]
	decision := {
		"rule_id": "ER-EFFECTS-NO-INLINE-USER-COMMAND",
		"reason": reason_for(d),
		"severity": "HIGH",
	}
}

reason_for(d) := msg if {
	msg := concat("", [
		"Your closing message hands the user a command as an inline shell block (", d, "). ",
		"User directive 2026-10-13: 'Update your standing order so that instead of providing me inline bash, you open a kitty tab for each inline bash block you want me to run, pre-pasted, with a comment block at the top saying why it should be ran'. ",
		"Open one tab per command block instead: python3 scripts/kitty-user-command.py --title '<short title>' --why '<why it should be run>' -- '<command>' ",
		"(repeat --why for more comment lines; --command-file for a command too long to pass inline). ",
		"Then end on prose that names which tabs you opened and what each one is for, with no shell block. ",
		"If a block is only a record of a command you already ran, drop it: the tool output shows it. ",
		"Re-fencing a command meant for the user as text does not change what it is.",
	])
}

detail := d if {
	startswith(raw, "INLINEUSERCOMMAND:")
	d := trim(trim_prefix(raw, "INLINEUSERCOMMAND:"), " \t\r\n")
} else := d if {
	raw != ""
	d := raw
}

raw := trim(signal_value, " \t\r\n")

signal_value := v if {
	v := input.signals.last_assistant_inline_user_command
	is_string(v)
} else := v if {
	v := input.signals.last_assistant_inline_user_command.output
	is_string(v)
} else := ""
