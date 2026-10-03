# METADATA
# scope: package
# title: Do not make an edit that would tear down a live Elden Ring run
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-SOURCE-EDIT-DURING-LIVE-RUN
#   description: >-
#     Refuse a Write/Edit while a run is live when that edit would kill the run.
#     `scripts/er-stale-run-sentinel.sh` runs from the PostToolUse hook and tears
#     down any run whose loaded DLLs the edited file feeds -- crate source, and
#     any file a crate's build reads (`include_str!`, a literal path in a build
#     script). That is the right invariant, since the run's code no longer matches
#     the tree, but it fires after the edit, so the first anyone knows of it is
#     the game closing.
#
#     The decision is the sentinel's own: the `live_er_run` signal asks its
#     `verdict` mode about the pending path, and this policy denies exactly a
#     `VERDICT TEARDOWN`. Until 2026-10-02 this file matched `crates/` alone and
#     promised docs stay editable mid-run, while the sentinel tore down for the
#     `docs/recon/*.tsv` that `er-game-base/build.rs` compiles in.
#
#     On 2026-09-12 the edit-kills-run pattern ran all session: the user drove a
#     run, the agent edited `er-quit-menu-core` to fix the next thing it had
#     found, and the run died under them.
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

signal_lines := split(object.get(input.signals, "live_er_run", ""), "\n")

# The signal prints the sentinel's own "[er-sentinel] LIVE: ..." block, or nothing.
# Absent/empty is the fail-open direction: no live run, nothing to protect.
live_run if {
	signal := object.get(input.signals, "live_er_run", "")
	contains(signal, "LIVE")
}

# The sentinel's verdict for the pending path: one
# `VERDICT <verdict>\t<branch>\t<detail>\t<profiles>` line from
# `er-stale-run-sentinel.sh verdict <path>`, the same function its PostToolUse
# `check` tears down on.
verdict_lines contains fields if {
	some line in signal_lines
	startswith(line, "VERDICT ")
	fields := split(substring(line, 8, -1), "\t")
}

has_verdict if count(verdict_lines) > 0

verdict_teardown if {
	some fields in verdict_lines
	fields[0] == "TEARDOWN"
}

verdict_why contains why if {
	some fields in verdict_lines
	count(fields) >= 3
	why := concat("", [fields[1], " -- ", fields[2]])
}

# With a verdict, it alone decides.
would_tear_down if {
	has_verdict
	verdict_teardown
}

# Without one -- no path in the event, or the classifier timed out -- fall back
# to the crate closure the signal prints instead, which can still attribute
# crate source. Anything outside `crates/` is let through on this path; the
# PostToolUse sentinel still runs after the edit.
would_tear_down if {
	not has_verdict
	dll_source
	feeds_a_loaded_dll
}

dll_source if {
	contains(file_path, "/crates/")
}

dll_source if {
	startswith(file_path, "crates/")
}

# The crates whose source compiles into a DLL this run loaded: one
# `CLOSURE crates/<name>` line per crate, straight out of the sentinel's own
# `closure` mode.
closure_crates contains crate if {
	some line in signal_lines
	startswith(line, "CLOSURE ")
	crate := trim_space(substring(line, 8, -1))
	crate != ""
}

# Matched on the directory prefix rather than the crate name so a crate whose
# name is a prefix of another (`er-invasion-warp` and `er-invasion-warp-core`)
# cannot be confused for it: the separator is part of the comparison.
feeds_a_loaded_dll if {
	some crate in closure_crates
	startswith(file_path, concat("", [crate, "/"]))
}

feeds_a_loaded_dll if {
	some crate in closure_crates
	contains(file_path, concat("", ["/", crate, "/"]))
}

# Fail closed when the signal named no crates either. A live run whose closure
# could not be computed is exactly the case the coarse rule was right about.
feeds_a_loaded_dll if {
	count(closure_crates) == 0
}

why_text := concat("; ", verdict_why) if {
	count(verdict_why) > 0
} else := "no verdict from the sentinel; fell back to the crate closure"

block_reason := "🧁 Cupcake blocked an edit while an Elden Ring run is LIVE. This edit would kill that run: `scripts/er-stale-run-sentinel.sh` runs from the PostToolUse hook and tears down any run whose loaded DLLs the edited file feeds -- crate source, and any file a crate's build reads (`include_str!`, a literal path in a build script). The invariant is right -- after the edit the run's code no longer matches the tree -- but it fires after the fact, so the user watches their game close.\n\nThis refusal is the sentinel's own verdict for this path (`scripts/er-stale-run-sentinel.sh verdict <path>`), so it refuses exactly what the teardown would act on. Paths the sentinel skips stay editable mid-run: `scripts/`, `.cupcake/`, prose and docs no build reads, gitignored files, and crates outside the run's dependency closure.\n\nPick one:\n  * finish reading the live run first -- the findings are the reason it is up;\n  * or end it deliberately and relaunch together:\n      python3 scripts/er-teardown.py > /dev/null 2>&1; python3 scripts/er-run-branch.py --with <pkg> ..."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	live_run
	would_tear_down

	decision := {
		"rule_id": "ER-EFFECTS-NO-SOURCE-EDIT-DURING-LIVE-RUN",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nTarget: ", file_path, "\nSentinel: ", why_text]),
	}
}
