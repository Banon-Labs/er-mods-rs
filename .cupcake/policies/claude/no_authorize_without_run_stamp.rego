# METADATA
# scope: package
# title: Ban asking the user to authorize a push the runtime-evidence gate will refuse
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-AUTHORIZE-WITHOUT-RUN-STAMP
#   description: >-
#     User, 2026-10-16: "I knew it needed a run stamp to push. I was hoping you would figure it
#     out (again) prior to asking me to authorize a push". The agent asked for `authorize <sha>`
#     on a branch whose commits change crates/, and the push was then refused by the pre-push
#     hook (scripts/check-runtime-evidence.sh) and by ER-EFFECTS-REQUIRE-RUNTIME-EVIDENCE,
#     because no DLL log carried `build git=<sha>`. Producing that log -- build at the tip,
#     launch, let the DLL write -- needed nothing from the user.
#
#     The signal reads the closing prose for `authorize <7..40 hex>`, resolves the sha in the
#     session's checkout or any `git worktree list` checkout, and runs that checkout's
#     check-runtime-evidence.sh on it. Exit 1 halts; exit 0, any other status, an unknown sha or
#     a message with no `authorize` token allows. The matching lives in the signal: a regex in a
#     rego rule can trap cupcake's WASM runtime and silence every policy at once (bd
#     a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14).
#   routing:
#     required_events: ["Stop"]
#     required_signals: ["last_assistant_authorize_without_run_stamp"]
package cupcake.policies.claude.no_authorize_without_run_stamp

import rego.v1

halt contains decision if {
	input.hook_event_name == "Stop"
	raw != ""
	decision := {
		"rule_id": "ER-EFFECTS-NO-AUTHORIZE-WITHOUT-RUN-STAMP",
		"reason": reason,
		"severity": "HIGH",
	}
}

reason := concat("", [
	"You asked the user for `authorize ", sha, "`, and no run has stamped that commit: `bash scripts/check-runtime-evidence.sh ", sha,
	"` exits 1 in ", checkout, " (", note, "). ",
	"The push it would authorize is refused by the pre-push hook and by ER-EFFECTS-REQUIRE-RUNTIME-EVIDENCE until a DLL log carries `build git=", sha, "`, ",
	"and producing that log needs nothing from the user. Stamp the run first: commit, build the changed shells at the tip with `bash scripts/er-build-dlls.sh <package>` (in the background; a cold cross-compile takes minutes), ",
	"launch them with `python3 scripts/er-run-branch.py --with <package>` and let the DLL write its log, then confirm `bash scripts/check-runtime-evidence.sh ", sha, "` exits 0. ",
	"Only then ask for the authorization: run the changed DLL built at that commit in game first, then ask. ",
	"User, 2026-10-16: 'I knew it needed a run stamp to push. I was hoping you would figure it out (again) prior to asking me to authorize a push'. ",
	"User, 2026-10-19: 'to authorize a push, you must have runtime evidence'.",
])

# `AUTHNORUNSTAMP:<sha>\t<checkout>\t<note>`, split with string operations only.
fields := split(trim_prefix(raw, "AUTHNORUNSTAMP:"), "\t")

sha := field(0, "the commit")

checkout := field(1, "its checkout")

note := field(2, "no DLL log names this commit")

field(i, fallback) := v if {
	count(fields) > i
	v := trim(fields[i], " \t\r\n")
	v != ""
} else := fallback

raw := trim(signal_value, " \t\r\n")

signal_value := v if {
	v := input.signals.last_assistant_authorize_without_run_stamp
	is_string(v)
} else := v if {
	v := input.signals.last_assistant_authorize_without_run_stamp.output
	is_string(v)
} else := ""
