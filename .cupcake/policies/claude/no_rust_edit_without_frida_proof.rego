# METADATA
# scope: package
# title: No Rust edit under crates/ without a Frida measurement behind it
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-RUST-EDIT-WITHOUT-FRIDA-PROOF
#   description: >-
#     Refuse a Write/Edit to `crates/**/*.rs` unless a Frida session has attached to
#     the game and reported observations since the last commit.
#
#     User directive 2026-09-16. AGENTS.md has said it for months -- "The order is
#     Frida, then Frida, then Frida, and only then a DLL: prototype with it, run the
#     experiment with it, and fix the thing with it if a hook can. Build a DLL when
#     the mechanism is already known and the code is the product, never to find
#     something out" -- and on this day the agent did the opposite twice in one
#     session: it tore the user's game down, ran a build, relaunched, and did it again,
#     to land a change whose mechanism it had never measured. The justification it gave
#     itself was an argument about a prior A/B it had already shown was confounded.
#     The user's answer was that a note is not a rule, so here is the rule.
#
#     The tell this encodes: when the reason for an edit is reasoning rather than a
#     measurement, the edit is a guess, and a guess here costs a teardown, a build and
#     a relaunch of somebody's game -- against an agent file that reloads in place for
#     nothing.
#
#     What opens the gate is `scripts/er-frida-evidence.py --check` printing PROVEN,
#     which needs a session that attached to a pid and received at least one message,
#     recorded after HEAD's commit time. The log lives under XDG_STATE_HOME, not in
#     the repo, so the Write tool being gated cannot forge its own permission. A commit
#     spends the evidence: one measurement licenses one change.
#
#     Deliberately NOT carved out: tests, doc comments, host-only crates. Every carve-out
#     is a door, and the failure this exists to stop was the agent walking through the
#     door it argued for itself. `scripts/`, `.cupcake/`, docs and every non-Rust file
#     stay editable, which is what makes it possible to build and fix this gate.
#
#     A SECOND INSTRUMENT was added 2026-09-19, and it is not one of those doors. It
#     exempts no file: it accepts a different measurement, taken at the branch by our own
#     DLL and quoted verbatim out of a live run's log, for the class Frida physically
#     cannot see -- our own unexported internals in a `cdylib` that exports only `DllMain`.
#     It is strictly narrower than the Frida path, because it opens the one crate whose
#     log it came from rather than the whole tree. The three conditions that keep it a
#     measurement (verbatim line, log newer than the last committed Rust change, real
#     crate) are enforced when the record is written, by the evidence reader.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Write", "Edit", "MultiEdit", "NotebookEdit"]
#     required_signals: ["frida_evidence"]
package cupcake.policies.claude.no_rust_edit_without_frida_proof

import rego.v1

# Defaulted at both levels for the same reason the signal below is: `input.tool_input` read
# directly is undefined when the key is absent, and an undefined term anywhere in the deny body
# takes the whole rule with it. Nothing in this file may depend on a key being present.
file_path := object.get(object.get(input, "tool_input", {}), "file_path", "")

# Rust source that compiles into something the game loads. The gate is the file
# extension plus the directory, so a `.toml`, a `.md` or a script is never caught.
rust_under_crates if {
	endswith(file_path, ".rs")
	contains(file_path, "/crates/")
}

rust_under_crates if {
	endswith(file_path, ".rs")
	startswith(file_path, "crates/")
}

# The verdict line from `scripts/er-frida-evidence.py --check`.
#
# Read through a defaulted `signals` object rather than `input.signals` directly. The
# engine attaches signals for a policy that declares `required_signals`, but the direct
# form is undefined when the key is absent, and an undefined term in the deny body below
# makes the whole rule undefined -- the gate would open on exactly the input that proves
# least. Measured by `test_deny_when_the_signals_object_is_missing_entirely`, which failed
# against the direct form.
signals := object.get(input, "signals", {})

raw_evidence := object.get(signals, "frida_evidence", "")

# A signal whose command exits non-zero does not reach a policy as its output at all: cupcake
# replaces the string with a failure record, an object carrying `error`, `exit_code`, `output`
# and `success`. Every string operation on that object is then undefined, and an undefined term
# in the deny body takes the whole rule with it -- so the gate opens on exactly the input that
# proves least.
#
# That is not hypothetical. Measured 2026-09-16 against cupcake 0.5.2: this signal script was the
# only one in `.cupcake/signals/` without its executable bit, an auto-discovered signal is exec'd
# directly rather than through `bash`, and the result was `Signal 'frida_evidence' failed with
# exit code 126` on every evaluation. `cupcake eval` allowed a `crates/` edit with no measurement
# behind it while `opa test` was green, because the tests all handed the rule a string.
#
# Coercing a non-string to the empty string makes that case deny instead, and the reason below
# says which of the two nothings it was.
evidence := raw_evidence if {
	is_string(raw_evidence)
}

evidence := "" if {
	not is_string(raw_evidence)
}

# Anything that is not a `PROVEN` line leaves this undefined and the deny below fires --
# including an empty signal, which is what a broken or timed-out reader produces. Failing
# closed is the point: a gate that opens when its evidence reader breaks is not a gate.
#
# A Frida verdict opens every crate, because the instrument reaches the game and the game is what
# all of this eventually talks to.
proven if {
	startswith(evidence, "PROVEN")
	not telemetry_verdict
}

# A telemetry verdict opens exactly one crate: the shell whose own log the quoted line came out of.
#
# The blind spot this closes, measured 2026-09-19. Frida reaches the GAME; it does not reach our
# own DLLs. A release `cdylib` here exports `DllMain` and nothing else, so an unexported Rust
# static, a `pub(crate)` seam, or "which of our functions calls which of our setters" has no
# address to attach to and no name to resolve. `er-save-game-row` served its destination browser
# undressed for exactly that reason -- `gfx_swap::set_profile_05_010_edit_armed` has one caller and
# that shell does not go through it -- and the gate answered a provable defect by demanding an
# instrument which cannot see it. An agent facing that either stalls or reaches for one of the
# forgery routes `scripts/er-frida-evidence.py` names in its own docstring, and neither is the
# behaviour this was written to get.
#
# What makes it evidence rather than an argument is enforced at record time, not here: the quoted
# line must be present verbatim in the named log, the log must be newer than the last committed
# Rust change, and the crate must exist. What is enforced HERE is the scope -- a measurement of one
# shell's branch says nothing about any other crate, so it may not open one. That makes this path
# narrower than the Frida path above, which opens the whole tree.
proven if {
	telemetry_verdict
	contains(file_path, concat("", ["crates/", licensed_crate, "/"]))
}

# Keyed on the two fixed words alone, so a verdict that has lost its `crate=` field is still
# recognised as telemetry and is refused by the rule above rather than falling through to the
# unscoped one.
telemetry_verdict if {
	startswith(evidence, "PROVEN telemetry")
}

# Anchored at the front of the verdict: everything to the right of the crate name is free text
# from a log, and free text must not be able to impersonate the field that decides scope.
licensed_crate := name if {
	matches := regex.find_all_string_submatch_n(`^PROVEN telemetry crate=([A-Za-z0-9_-]+)(?: |$)`, evidence, 1)
	count(matches) == 1
	name := matches[0][1]
}

block_reason := "🧁 Cupcake blocked a Rust edit with no Frida measurement behind it. AGENTS.md: \"The order is Frida, then Frida, then Frida, and only then a DLL: prototype with it, run the experiment with it, and fix the thing with it if a hook can. Build a DLL when the mechanism is already known and the code is the product, never to find something out.\"\n\nGo and look first:\n  python3 scripts/er-frida-up.py\n  uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/<agent>.js\n\nThe watch records what it saw on exit, and `python3 scripts/er-frida-evidence.py --check` is what opens this gate. It needs a session that attached to a pid and received at least one message -- a watch that observed nothing did not look at anything. A commit spends the evidence, so the next change needs its own measurement.\n\nIf the mechanism is inside one of OUR DLLs rather than in the game -- an unexported static, a `pub(crate)` seam, which of our functions calls which of our setters -- Frida has nothing to attach to, and the instrument that reaches it is the shell's own in-process telemetry from a live run:\n  python3 scripts/er-frida-evidence.py --record-telemetry --crate <crate> --log <the run's log> --line '<a line from it, verbatim>'\nThe line must be in that log word for word and the log must be newer than the last committed Rust change. It opens that ONE crate.\n\nIf neither instrument can reach it, say so in one sentence and say what can. Do not edit around this by narrowing the change until it looks harmless.\n\nEditable without proof: `scripts/`, `.cupcake/`, docs, and every non-Rust file."

# What the refusal quotes back. The three cases are worth telling apart: a verdict line is the
# reader answering, an absent signal is nobody having asked, and a failure record is the reader
# being broken -- which is a bug to fix rather than a measurement to go and take.
said := evidence if {
	is_string(raw_evidence)
	raw_evidence != ""
}

said := "<signal absent>" if {
	is_string(raw_evidence)
	raw_evidence == ""
}

said := "<the frida_evidence signal failed; cupcake replaced its output with a failure record, so it exited non-zero. Check that .cupcake/signals/frida_evidence.sh is executable and runs>" if {
	not is_string(raw_evidence)
}

# The tools that only look. Everything else carrying a `crates/**/*.rs` path is treated as a
# write, so a write tool nobody thought to list is still refused.
#
# Routing metadata was the whole guard until 2026-09-16, when a subagent reading
# `crates/er-telemetry-core/src/counters.rs` with the Read tool was refused by this policy and
# could not find anything in `.cupcake/` that explained it. A gate whose purpose is to make an
# agent go and look must never be the thing that stops it looking, and a reader has nothing to
# offer as evidence because it changes nothing.
#
# The previous shape was argued for rather than measured: a test here recorded that a hand-built
# Read event does reach the deny, and reasoned that it did not matter because routing keeps Read
# out. It did matter. Routing is an optimisation; the deny body is the contract. Listing readers
# rather than writers keeps the fail-closed direction the old argument was right about.
read_only_tools := {"Read", "Grep", "Glob", "NotebookRead", "WebFetch", "WebSearch", "LSP"}

tool_name := object.get(input, "tool_name", "")

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	not read_only_tools[tool_name]
	rust_under_crates
	not proven

	decision := {
		"rule_id": "ER-EFFECTS-NO-RUST-EDIT-WITHOUT-FRIDA-PROOF",
		"severity": "HIGH",
		"reason": concat("", [
			block_reason,
			"\n\nTarget: ",
			file_path,
			"\nEvidence reader said: ",
			said,
		]),
	}
}
