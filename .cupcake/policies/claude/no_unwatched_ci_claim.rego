# METADATA
# scope: package
# title: Ban saying CI is still running when nothing in the session is following it
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-UNWATCHED-CI-CLAIM
#   description: >-
#     User correction 2026-11-25: "\" CI on GitHub is still running\" I don't believe you because
#     your rego policy that told you to open a kitty tab/split to tail the output didn't run". The
#     closing message said "CI on GitHub is still running; `compiles` and `cargo-test` hadn't
#     finished at last check." from one read of the UserPromptSubmit CI STATE line. No background
#     task was watching the checks and no kitty split tailed one, so the claim named a running job
#     the session was not following, and the background-task guards had nothing to fire on.
#
#     The signal halts a closing message that says CI is running, pending, queued, waited on or
#     unfinished (quotes folded away; a finished report, passing or failing, is not a claim) unless a
#     background Bash or Monitor task still running follows that PR's checks: `gh pr checks <n>
#     --watch`, `gh run watch <run-id>`, or an until/while loop polling them. The halt quotes the
#     claim and names the watcher to start and the tail split to open; no_untailed_long_run then
#     requires the split.
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_ci_running_claim"]
package cupcake.policies.claude.no_unwatched_ci_claim

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	some c in [claim]
	decision := {
		"rule_id": "ER-EFFECTS-NO-UNWATCHED-CI-CLAIM",
		"reason": reason_for(c),
		"severity": "HIGH",
	}
}

reason_for(c) := msg if {
	msg := concat("", [
		"The closing message says CI is still running, and nothing in this session is following it: ", c,
		". A CI STATE line or one `gh pr checks` read is a snapshot, not a watch: start the watcher in this turn, before the closing message.",
	])
}

claim := s if {
	startswith(raw, "UNWATCHEDCI:")
	s := trim(trim_prefix(raw, "UNWATCHEDCI:"), " \t\r\n")
	s != ""
}

raw := trim(signal_value, " \t\r\n")

signal_value := v if {
	v := input.signals.last_assistant_ci_running_claim
	is_string(v)
} else := v if {
	v := input.signals.last_assistant_ci_running_claim.output
	is_string(v)
} else := ""
