# OPA unit tests for edit_no_tmp_scripts_guard.
#
# Run with:
#   opa test .cupcake/policies/claude/edit_no_tmp_scripts_guard.rego \
#            .cupcake/tests/edit_no_tmp_scripts_guard_test.rego
package cupcake.policies.claude.edit_no_tmp_scripts_guard_test

import rego.v1

import data.cupcake.policies.claude.edit_no_tmp_scripts_guard as guard

write_event(path) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "write",
	"tool_input": {"path": path, "content": "test"},
}

claude_write_event(path) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Write",
	"tool_input": {"file_path": path, "content": "test"},
}

bash_event(command) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": command, "timeout": 30000, "description": "test case"},
}

rule_ids(denials) := {d.rule_id | some d in denials}

denied(input_event) if {
	denials := guard.deny with input as input_event
	"ER-EFFECTS-NO-TMP-SCRIPTS-GUARD" in rule_ids(denials)
}

test_deny_pi_write_tmp_ps1_path_key if {
	denied(write_event("/tmp/check-er-net-effects.ps1"))
}

test_deny_claude_write_tmp_python_file_path_key if {
	denied(claude_write_event("/tmp/ghidra_probe.py"))
}

test_deny_bash_cat_tmp_script if {
	denied(bash_event("cat > /tmp/check-er-net-effects.ps1 <<'EOF'\nWrite-Output hi\nEOF"))
}

test_deny_bash_python_tmp_script if {
	denied(bash_event("python3 - <<'PY'\nopen('/tmp/probe.py','w').write('print(1)')\nPY"))
}

test_allow_tmp_json_artifact if {
	not denied(write_event("/tmp/check-er-net-effects.json"))
}

test_allow_repo_script if {
	not denied(write_event("/home/choza/projects/er-mods-rs/scripts/check-er-net-effects.ps1"))
}

test_allow_bash_read_tmp_script_path_without_authoring_verb if {
	not denied(bash_event("powershell.exe -File /tmp/existing-user-script.ps1"))
}

test_allow_non_pretooluse_event if {
	denials := guard.deny with input as {
		"hook_event_name": "PostToolUse",
		"tool_name": "write",
		"tool_input": {"path": "/tmp/x.ps1"},
	}
	count(denials) == 0
}

# ---------------------------------------------------------------------------
# READS out of /tmp are not authoring. Every case below is a measured or exactly
# analogous false positive of the old "interpreter word anywhere + /tmp path
# anywhere" test (2026-08-25).
# ---------------------------------------------------------------------------

# The original report: a heredoc that only READS third-party js downloaded into /tmp.
test_allow_bash_heredoc_reading_tmp_js if {
	not denied(bash_event(concat("\n", [
		"python3 - <<'EOF'",
		"import re,glob",
		"for p in sorted(glob.glob('/tmp/planner/*.js')):",
		"    t=open(p,encoding='utf-8',errors='replace').read()",
		"    print(len(t))",
		"EOF",
	])))
}

# The second report: no interpreter at all -- "bun" matched inside "planner-bundle",
# and /tmp is the SOURCE of the copy, not its destination.
test_allow_bash_cp_out_of_tmp_into_repo if {
	not denied(bash_event("mkdir -p target/planner-bundle && cp -f /tmp/planner/*.js target/planner-bundle/ && ls target/planner-bundle"))
}

test_allow_bash_node_runs_tmp_script if {
	not denied(bash_event("node /tmp/x.js"))
}

test_allow_bash_python3_runs_tmp_script if {
	not denied(bash_event("python3 /tmp/x.py"))
}

test_allow_bash_cat_reads_tmp_script if {
	not denied(bash_event("cat /tmp/x.sh"))
}

test_allow_bash_grep_reads_tmp_script if {
	not denied(bash_event("grep -n pattern /tmp/x.py"))
}

test_allow_bash_glob_over_tmp_scripts if {
	not denied(bash_event("ls -la /tmp/planner/*.js"))
}

test_allow_bash_python_open_read_mode if {
	not denied(bash_event("python3 -c \"print(open('/tmp/planner/a.js').read()[:20])\""))
}

test_allow_bash_python_open_encoding_kwarg_not_a_mode if {
	not denied(bash_event("python3 -c \"open('/tmp/planner/a.js', encoding='utf-8', errors='replace')\""))
}

test_allow_bash_mv_out_of_tmp if {
	not denied(bash_event("mv /tmp/x.py scripts/x.py"))
}

# Word-boundary regressions: an interpreter/command name buried in an unrelated word
# must not arm the rule even when a /tmp script path is present.
test_allow_bash_command_word_only_as_substring if {
	not denied(bash_event("cargo run --bin nodemon-concatenate -- --committee /tmp/planner/x.js"))
}

# Data ARTIFACTS into /tmp remain allowed -- only source/script extensions are blocked.
test_allow_bash_redirect_data_artifact_into_tmp if {
	not denied(bash_event("python3 scripts/probe.py > /tmp/out.json"))
}

# ---------------------------------------------------------------------------
# WRITES into /tmp are still denied.
# ---------------------------------------------------------------------------

test_deny_bash_append_redirect_tmp_script if {
	denied(bash_event("printf 'set -e\\n' >> /tmp/x.sh"))
}

test_deny_bash_echo_redirect_tmp_script if {
	denied(bash_event("echo 'print(1)' > /tmp/probe.py"))
}

test_deny_bash_stderr_redirect_tmp_script if {
	denied(bash_event("./build.sh 2>/tmp/wrapper.sh"))
}

test_deny_bash_tee_tmp_script if {
	denied(bash_event("echo 'print(1)' | tee -a /tmp/probe.py"))
}

test_deny_bash_curl_output_flag_tmp_script if {
	denied(bash_event("curl -sL https://example.invalid/x.js -o /tmp/planner/x.js"))
}

test_deny_bash_curl_long_output_flag_tmp_script if {
	denied(bash_event("curl -sL https://example.invalid/x.js --output /tmp/planner/x.js"))
}

test_deny_bash_wget_output_flag_tmp_script if {
	denied(bash_event("wget -O /tmp/planner/x.js https://example.invalid/x.js"))
}

test_deny_bash_cp_into_tmp_script if {
	denied(bash_event("cp scripts/probe.py /tmp/probe.py"))
}

test_deny_bash_mv_into_tmp_script if {
	denied(bash_event("mv probe.sh /tmp/probe.sh && bash /tmp/probe.sh"))
}

test_deny_bash_install_into_tmp_script if {
	denied(bash_event("install -m 755 probe.sh /tmp/probe.sh"))
}

test_deny_bash_pathlib_write_text_tmp_script if {
	denied(bash_event("python3 -c \"from pathlib import Path; Path('/tmp/probe.py').write_text('print(1)')\""))
}

# ---------------------------------------------------------------------------
# Extracting a committed file into /tmp is not authoring it (2026-10-22). The
# first case is the measured refusal, with the session id filled in.
# ---------------------------------------------------------------------------

scratchpad := "/tmp/claude-1000/-home-banon-projects-er-mods-rs/fbbd7ff7-ace9-472a-bf63-ad98e5e1feca/scratchpad"

test_allow_bash_git_show_rev_path_into_scratchpad if {
	not denied(bash_event(concat("", [
		"git show main:.cupcake/tests/teardown_must_relaunch_test.rego > ",
		scratchpad, "/old_test.rego",
	])))
}

test_allow_bash_git_cat_file_rev_path_into_tmp if {
	not denied(bash_event("git cat-file -p HEAD~3:scripts/check.sh > /tmp/old-check.sh"))
}

test_allow_bash_git_dash_c_show_rev_path_into_tmp if {
	not denied(bash_event("git -C /home/u/repo show origin/main:scripts/x.py >/tmp/x.py"))
}

# The same extraction with anything else in the command still denies.

test_deny_bash_git_show_then_chained_run if {
	denied(bash_event("git show main:scripts/x.py > /tmp/x.py && bash /tmp/x.py"))
}

test_deny_bash_git_show_then_hand_written_append if {
	denied(bash_event("git show main:scripts/x.py > /tmp/x.py; echo 'print(1)' >> /tmp/x.py"))
}

test_deny_bash_git_show_then_newline_command if {
	denied(bash_event("git show main:scripts/x.py > /tmp/x.py\nprintf 'x' >> /tmp/x.py"))
}

test_deny_bash_git_show_piped_through_an_edit if {
	denied(bash_event("git show main:scripts/x.py | sed 's/a/b/' > /tmp/x.py"))
}

test_deny_bash_git_show_command_substitution if {
	denied(bash_event("git show main:$(cat /tmp/name) > /tmp/x.py"))
}

test_deny_bash_git_show_backtick_substitution if {
	denied(bash_event("git show main:`cat /tmp/name` > /tmp/x.py"))
}

test_deny_bash_git_show_append_redirect if {
	denied(bash_event("git show main:scripts/x.py >> /tmp/x.py"))
}

# `:<path>` reads the index, which `git add` fills with whatever the agent wrote.
test_deny_bash_git_show_index_blob if {
	denied(bash_event("git show :scripts/x.py > /tmp/x.py"))
}

# A leading dash is an option, not a revision.
test_deny_bash_git_show_option_shaped_rev if {
	denied(bash_event("git show --output=/tmp/a.txt:x > /tmp/x.py"))
}

# Only `show` and `cat-file -p` of a `<rev>:<path>` blob are extraction.
test_deny_bash_git_log_patch_into_tmp_script if {
	denied(bash_event("git log -p > /tmp/x.py"))
}

# The shape has to be the command, not words inside one.
test_deny_bash_echo_of_git_show_text_into_tmp_script if {
	denied(bash_event("echo git show main:x.py > /tmp/x.py"))
}

# ---------------------------------------------------------------------------
# A write reached through a variable bound to a /tmp path earlier in the same
# command (2026-11-07). The first case is the measured escape: the literal
# spelling of the same command was refused, and this one wrote probe_ash.py
# into the scratchpad.
# ---------------------------------------------------------------------------

measured_scratchpad := "/tmp/claude-1000/-home-banon-projects-er-mods-rs/05652911-eb56-4e82-9deb-8ee3d73ad8fd/scratchpad"

test_deny_bash_redirect_through_variable_measured_escape if {
	denied(bash_event(concat("", [
		"cd /home/banon/projects/er-mods-rs && S=", measured_scratchpad,
		" && cat > $S/probe_ash.py <<'EOF'\nimport sys\nEOF\npython3 $S/probe_ash.py",
	])))
}

test_deny_bash_redirect_through_braced_variable if {
	denied(bash_event(concat("", [
		"S=", measured_scratchpad, "; cat > ${S}/probe_ash.py <<'EOF'\nimport sys\nEOF",
	])))
}

test_deny_bash_redirect_through_quoted_variable if {
	denied(bash_event("S=/tmp/work; echo 'print(1)' > \"$S/probe.py\""))
}

test_deny_bash_redirect_through_variable_with_quoted_value if {
	denied(bash_event("export S=\"/tmp/work/\"\necho 'print(1)' >> $S/probe.py"))
}

test_deny_bash_redirect_through_variable_naming_whole_file if {
	denied(bash_event("F=/tmp/probe.sh && printf 'set -e\\n' > $F"))
}

test_deny_bash_tee_through_variable if {
	denied(bash_event("S=/tmp/work && echo 'print(1)' | tee -a $S/probe.py"))
}

test_deny_bash_cp_into_tmp_through_variable if {
	denied(bash_event("S=/tmp/work; cp scripts/probe.py $S/probe.py"))
}

test_deny_bash_curl_output_through_variable if {
	denied(bash_event("D=/tmp/planner && curl -sL https://example.invalid/x.js -o ${D}/x.js"))
}

test_deny_bash_pathlib_write_through_variable if {
	denied(bash_event("S=/tmp/work; python3 -c \"from pathlib import Path; Path('$S/probe.py').write_text('x')\""))
}

# A bare `/tmp` value joins to `/tmp/<rest>`.
test_deny_bash_redirect_through_variable_bound_to_bare_tmp if {
	denied(bash_event("T=/tmp; echo 'print(1)' > $T/probe.py"))
}

# The binding in force at the write is the latest one before it.
test_deny_bash_redirect_through_variable_rebound_into_tmp if {
	denied(bash_event("S=scripts; S=/tmp/work; echo 'print(1)' > $S/probe.py"))
}

# Data ARTIFACTS through the same variable stay allowed.
test_allow_bash_redirect_data_artifact_through_tmp_variable if {
	not denied(bash_event(concat("", [
		"S=", measured_scratchpad, " && python3 scripts/probe.py > $S/out.txt 2>$S/err.log",
	])))
}

test_allow_bash_redirect_data_artifact_through_braced_tmp_variable if {
	not denied(bash_event("S=/tmp/work; echo hi > ${S}/notes.txt; jq . a.json > ${S}/a.json"))
}

# A variable bound to a path outside /tmp is not this guard's business.
test_allow_bash_redirect_script_through_repo_variable if {
	not denied(bash_event("W=.worktrees/pr-x/scripts; echo 'print(1)' > $W/probe.py"))
}

# Rebound out of /tmp before the write: the write lands in the repo.
test_allow_bash_redirect_through_variable_rebound_out_of_tmp if {
	not denied(bash_event("S=/tmp/work; S=scripts; echo 'print(1)' > $S/probe.py"))
}

# Bound only AFTER the write: the shell expanded the inherited value, which this
# command does not show.
test_allow_bash_redirect_through_variable_bound_after_the_write if {
	not denied(bash_event("echo 'print(1)' > $S/probe.py; S=/tmp/work"))
}

# Never bound in the command at all.
test_allow_bash_redirect_through_unbound_variable if {
	not denied(bash_event("echo 'print(1)' > $OUT/probe.py"))
}

# Reading a script through the variable is not authoring it.
test_allow_bash_run_tmp_script_through_variable if {
	not denied(bash_event("S=/tmp/work; python3 $S/probe.py > $S/out.json"))
}

# A name that only shares a prefix with the bound one is a different variable.
test_allow_bash_redirect_through_variable_sharing_a_prefix if {
	not denied(bash_event("S=/tmp/work; echo 'print(1)' > $SRC/probe.py"))
}
