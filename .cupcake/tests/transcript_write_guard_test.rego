# OPA unit tests for transcript_write_guard.
#
# Run with:
#   opa test .cupcake/policies/claude/transcript_write_guard.rego \
#            .cupcake/tests/transcript_write_guard_test.rego
package cupcake.policies.claude.transcript_write_guard_test

import rego.v1

import data.cupcake.policies.claude.transcript_write_guard as guard

t := "/home/u/.claude/projects/-home-u-repo/abc.jsonl"

bash(command) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": command},
}

tool(name, path) := {
	"hook_event_name": "PreToolUse",
	"tool_name": name,
	"tool_input": {"file_path": path, "content": "x"},
}

denied(event) if {
	some d in guard.deny with input as event
	d.rule_id == "ER-EFFECTS-TRANSCRIPT-WRITE-GUARD"
}

test_write_tool_on_transcript_denies if denied(tool("Write", t))

test_edit_tool_on_transcript_denies if denied(tool("Edit", t))

test_write_tool_on_auto_memory_allowed if {
	not denied(tool("Write", "/home/u/.claude/projects/-home-u-repo/memory/MEMORY.md"))
}

test_redirect_into_transcript_denies if denied(bash(concat("", ["echo '{}' >> ", t])))

test_glued_redirect_into_transcript_denies if denied(bash("echo x >~/.claude/projects/-home-u-repo/abc.jsonl"))

test_tee_into_transcript_denies if denied(bash(concat("", ["echo x | tee -a ", t])))

test_bare_tee_into_transcript_denies if denied(bash("echo x | tee ~/.claude/projects/-home-u-repo/abc.jsonl"))

test_sed_in_place_denies if denied(bash(concat("", ["sed -i 's/a/b/' ", t])))

test_copy_over_transcript_denies if denied(bash(concat("", ["cp -f /tmp/forged.jsonl ", t])))

test_reading_transcript_allowed if {
	not denied(bash(concat("", ["jq -c . ", t, " 2>/dev/null | tail -3"])))
}

test_reading_into_scratch_allowed if {
	not denied(bash(concat("", ["jq -c . ", t, " > /tmp/out.json"])))
}

test_copying_transcript_out_allowed if {
	not denied(bash(concat("", ["cp -f ", t, " /tmp/copy.jsonl"])))
}

test_tee_elsewhere_allowed if {
	not denied(bash(concat("", ["cat ", t, " | tee /tmp/copy.jsonl"])))
}

test_unrelated_command_allowed if {
	not denied(bash("sed -i 's/a/b/' notes.txt"))
}
