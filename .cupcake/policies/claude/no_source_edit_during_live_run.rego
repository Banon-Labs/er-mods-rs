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

# Only source that compiles into a DLL. Paths the sentinel treats as inert
# (`scripts/`, `.cupcake/`, docs) stay editable mid-run, which is what makes it
# possible to write this very policy while a run is up.
dll_source if {
	contains(file_path, "/crates/")
}

dll_source if {
	startswith(file_path, "crates/")
}

# The crates whose source compiles into a DLL this run loaded, as the signal reports
# them: one `CLOSURE crates/<name>` line per crate, straight out of the sentinel's own
# `closure` mode. Same set the PostToolUse classifier decides teardown on, so the two
# cannot drift.
closure_crates contains crate if {
	some line in split(object.get(input.signals, "live_er_run", ""), "\n")
	startswith(line, "CLOSURE ")
	crate := trim_space(substring(line, 8, -1))
	crate != ""
}

# Is the edited file inside one of those crates? Matched on the directory prefix
# rather than the crate name so a crate whose name is a prefix of another
# (`er-invasion-warp` and `er-invasion-warp-core`) cannot be confused for it: the
# separator is part of the comparison.
feeds_a_loaded_dll if {
	some crate in closure_crates
	startswith(file_path, concat("", [crate, "/"]))
}

feeds_a_loaded_dll if {
	some crate in closure_crates
	contains(file_path, concat("", ["/", crate, "/"]))
}

# Fail closed when the signal named no crates at all. A live run whose closure could
# not be computed is exactly the case the coarse rule was right about, and the signal
# prints nothing on a timeout or a broken cargo metadata. `count == 0` with a live run
# means the classifier could not answer, not that the run loads nothing.
feeds_a_loaded_dll if {
	count(closure_crates) == 0
}

block_reason := "🧁 Cupcake blocked a source edit while an Elden Ring run is LIVE. This edit would kill that run: `scripts/er-stale-run-sentinel.sh` runs from the PostToolUse hook and tears down any run whose loaded DLLs the edited file feeds. The invariant is right -- after the edit the run's code no longer matches the tree -- but it fires after the fact, so the user watches their game close.\n\nOn 2026-09-12 this ran all session: the user drove a run, the agent edited `er-quit-menu-core` to fix the next thing it had spotted, and the run died under them.\n\nPick one:\n  * finish reading the live run first -- the findings are the reason it is up;\n  * or end it deliberately and relaunch together:\n      python3 scripts/er-teardown.py > /dev/null 2>&1; python3 scripts/er-run-branch.py --with <pkg> ...\n\n`scripts/`, `.cupcake/` and docs stay editable mid-run; the sentinel treats them as inert."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	live_run
	dll_source
	feeds_a_loaded_dll

	decision := {
		"rule_id": "ER-EFFECTS-NO-SOURCE-EDIT-DURING-LIVE-RUN",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nTarget: ", file_path]),
	}
}
