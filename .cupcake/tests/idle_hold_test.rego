# OPA unit tests for idle_hold (the Stop-event halt on an unjustified idle/hold announcement).
# Run with:
#   opa test .cupcake/system/commands.rego \
#     .cupcake/policies/claude/idle_hold.rego \
#     .cupcake/tests/idle_hold_test.rego
package cupcake.policies.claude.idle_hold_test

import rego.v1

import data.cupcake.policies.claude.idle_hold as guard

stop_event(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_idle_hold": sig},
}

stop_event_object_signal(sig) := {
	"hook_event_name": "Stop",
	"signals": {"last_assistant_idle_hold": {"output": sig, "exit_code": 0}},
}

rule_ids(halts) := {d.rule_id | some d in halts}

# A tagged idle-hold hit halts the turn-end.
test_halt_on_idlehold_tagged_signal if {
	halts := guard.halt with input as stop_event("IDLEHOLD:standing by")
	"ER-EFFECTS-NO-IDLE-HOLD" in rule_ids(halts)
}

# Bare/untagged non-empty value (backward compat with crafted signal shapes) still halts.
test_halt_on_bare_string_signal if {
	halts := guard.halt with input as stop_event("I'm holding")
	"ER-EFFECTS-NO-IDLE-HOLD" in rule_ids(halts)
}

# Object-shaped signal ({output: ...}) is handled too.
test_halt_on_object_signal if {
	halts := guard.halt with input as stop_event_object_signal("IDLEHOLD:holding for")
	"ER-EFFECTS-NO-IDLE-HOLD" in rule_ids(halts)
}

# The halt reason nudges toward non-overlapping work and the justification escape hatch.
test_reason_mentions_nonoverlapping_and_justification if {
	halts := guard.halt with input as stop_event("IDLEHOLD:standing by")
	some d in halts
	contains(d.reason, "NON-OVERLAPPING")
	contains(d.reason, "would normally have done")
}

# A VERBOSEPAUSE tag halts under the distinct verbose-pause rule.
test_halt_on_verbosepause_signal if {
	halts := guard.halt with input as stop_event("VERBOSEPAUSE:612")
	"ER-EFFECTS-NO-VERBOSE-PAUSE" in rule_ids(halts)
}

# A VERBOSEPAUSE tag must NOT also trip the idle-hold halt (the bare-fallback excludes it).
test_verbosepause_does_not_also_idlehold if {
	halts := guard.halt with input as stop_event("VERBOSEPAUSE:612")
	not "ER-EFFECTS-NO-IDLE-HOLD" in rule_ids(halts)
}

# The verbose-pause reason demands a short blocked-note and forbids status/plans, and echoes the count.
test_verbose_reason_mentions_short_and_terse if {
	halts := guard.halt with input as stop_event("VERBOSEPAUSE:612")
	some d in halts
	d.rule_id == "ER-EFFECTS-NO-VERBOSE-PAUSE"
	contains(d.reason, "short")
	contains(d.reason, "blocked")
	contains(d.reason, "612")
}

# A VERBOSEPAUSE object-shaped signal ({output: ...}) halts too.
test_halt_on_verbosepause_object_signal if {
	halts := guard.halt with input as stop_event_object_signal("VERBOSEPAUSE:900")
	"ER-EFFECTS-NO-VERBOSE-PAUSE" in rule_ids(halts)
}

# No idle hold -> no halt (empty signal).
test_no_halt_on_clean_turn if {
	halts := guard.halt with input as stop_event("")
	count(halts) == 0
}

# Whitespace-only signal is treated as clean.
test_no_halt_on_whitespace_signal if {
	halts := guard.halt with input as stop_event("   \n")
	count(halts) == 0
}

# The halt only applies to Stop events, not other events that might carry the signal.
test_no_halt_on_non_stop_event if {
	halts := guard.halt with input as {
		"hook_event_name": "PreToolUse",
		"signals": {"last_assistant_idle_hold": "IDLEHOLD:standing by"},
	}
	count(halts) == 0
}

# --- a pause names what it is blocked on (2026-11-11) -------------------------------------------

live_report := "Shells running: bx12 (agent a740eb3 (Speed up): Rerun the selftest; up 3m02s). Agents working: a740eb3 'Speed up' (running)."

# UNNAMEDPAUSE halts under its own rule and quotes the report, so the rewrite can name an item.
test_halt_on_unnamedpause if {
	halts := guard.halt with input as stop_event(concat("", ["UNNAMEDPAUSE:", live_report]))
	rule_ids(halts) == {"ER-EFFECTS-PAUSE-NAMES-BLOCKER"}
	some d in halts
	contains(d.reason, "bx12")
	contains(d.reason, "Speed up")
	contains(d.reason, "cupcake_session_tasks.py")
}

# The object-shaped signal halts the same way.
test_halt_on_unnamedpause_object_signal if {
	halts := guard.halt with input as stop_event_object_signal(concat("", ["UNNAMEDPAUSE:", live_report]))
	rule_ids(halts) == {"ER-EFFECTS-PAUSE-NAMES-BLOCKER"}
}

# A tag with nothing after it is not a report: no halt.
test_no_halt_on_empty_unnamedpause if {
	halts := guard.halt with input as stop_event("UNNAMEDPAUSE:  ")
	count(halts) == 0
}

# A report riding after VERBOSEPAUSE: one halt, the verbose one, whose reason carries the report;
# the char count is still read from the first line alone.
test_verbosepause_with_report_is_one_halt_quoting_it if {
	halts := guard.halt with input as stop_event(concat("", ["VERBOSEPAUSE:612\nUNNAMED:", live_report]))
	rule_ids(halts) == {"ER-EFFECTS-NO-VERBOSE-PAUSE"}
	some d in halts
	contains(d.reason, "(612 chars)")
	contains(d.reason, "bx12")
}

# A report riding after IDLEHOLD: one halt, the idle one, with the phrase read from the first line.
test_idlehold_with_report_is_one_halt_quoting_it if {
	halts := guard.halt with input as stop_event(concat("", ["IDLEHOLD:standing by\nUNNAMED:", live_report]))
	rule_ids(halts) == {"ER-EFFECTS-NO-IDLE-HOLD"}
	some d in halts
	contains(d.reason, "('standing by')")
	contains(d.reason, "a740eb3 'Speed up'")
}

# Without a report the existing reasons carry no naming tail.
test_no_name_tail_without_report if {
	halts := guard.halt with input as stop_event("VERBOSEPAUSE:612")
	some d in halts
	not contains(d.reason, "names nothing")
}
