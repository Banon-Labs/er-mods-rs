# METADATA
# scope: package
# title: Do not edit DLL source while an Elden Ring run is live
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-SOURCE-EDIT-DURING-LIVE-RUN
#   description: >-
#     Refuse a Write/Edit under `crates/` while a run is live, because that edit
#     kills the run. `scripts/er-stale-run-sentinel.sh` runs from the PostToolUse
#     hook and tears down any run whose loaded DLLs the edited file feeds -- the
#     right invariant, since the run's code no longer matches the tree, but it
#     fires AFTER the edit, so the first anyone knows of it is the game closing.
#
#     On 2026-09-12 that pattern ran all session: the user drove a run, the agent
#     edited `er-quit-menu-core` to fix the next thing it had found, and the run
#     died under them. The agent then blamed its own explicit teardown and wrote a
#     guard for that instead, which would not have stopped a single one of these.
#
#     The choice this forces is the real one: finish the observation, or end the
#     run deliberately and relaunch. Both are better than losing it by surprise.
#     Editing while nothing is running is untouched.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Write", "Edit", "MultiEdit", "NotebookEdit"]
#     required_signals: ["live_er_run"]
package cupcake.policies.claude.no_source_edit_during_live_run

import rego.v1

file_path := object.get(input.tool_input, "file_path", "")

# The signal prints the sentinel's own "[er-sentinel] LIVE: ..." block, or nothing.
# Absent/empty is the fail-open direction: no live run, nothing to protect.
live_run if {
	signal := object.get(input.signals, "live_er_run", "")
	contains(signal, "LIVE")
}

# Only source that compiles into a DLL. The sentinel's own classifier is finer than
# this -- it asks which package feeds which loaded DLL -- but a policy cannot shell
# out per path, and `crates/` is the directory whose edits it tears down for. Paths
# it treats as inert (`scripts/`, `.cupcake/`, docs) stay editable mid-run, which is
# what makes it possible to write this very policy while a run is up.
dll_source if {
	contains(file_path, "/crates/")
}

dll_source if {
	startswith(file_path, "crates/")
}

block_reason := "🧁 Cupcake blocked a source edit while an Elden Ring run is LIVE. This edit would kill that run: `scripts/er-stale-run-sentinel.sh` runs from the PostToolUse hook and tears down any run whose loaded DLLs the edited file feeds. The invariant is right -- after the edit the run's code no longer matches the tree -- but it fires after the fact, so the user watches their game close.\n\nOn 2026-09-12 this ran all session: the user drove a run, the agent edited `er-quit-menu-core` to fix the next thing it had spotted, and the run died under them.\n\nPick one:\n  * finish reading the live run first -- the findings are the reason it is up;\n  * or end it deliberately and relaunch together:\n      python3 scripts/er-teardown.py > /dev/null 2>&1; python3 scripts/er-run-branch.py --with <pkg> ...\n\n`scripts/`, `.cupcake/` and docs stay editable mid-run; the sentinel treats them as inert."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	live_run
	dll_source

	decision := {
		"rule_id": "ER-EFFECTS-NO-SOURCE-EDIT-DURING-LIVE-RUN",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nTarget: ", file_path]),
	}
}
