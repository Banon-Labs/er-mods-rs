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
# C is what Ghidra's decompiler writes (2026-11-10). A dump is data, so a C file
# in /tmp is refused only when the agent typed bytes that are not decompiler
# output. The first three cases are the measured commands of this session's
# subagents, with its scratchpad path: the first two were refused, the third was
# let through only because its target was relative.
# ---------------------------------------------------------------------------

repo := "/home/banon/projects/er-mods-rs"

session_scratchpad := "/tmp/claude-1000/-home-banon-projects-er-mods-rs/5ec7b779-1950-45a6-84dd-ef5b2c3e9082/scratchpad"

bash_event_in(cwd, command) := object.union(bash_event(command), {"cwd": cwd})

write_with(tool, path, text) := {
	"hook_event_name": "PreToolUse",
	"tool_name": tool,
	"cwd": repo,
	"tool_input": {"file_path": path, "content": text},
}

edit_with(path, text) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Edit",
	"cwd": repo,
	"tool_input": {"file_path": path, "old_string": "x", "new_string": text},
}

ghidra_dump := concat("\n", [
	"/* WARNING: Unknown calling convention -- yet parameter storage is locked */",
	"",
	"int HksEnv(ChrIns **chrInsHolder,HksEnvId envId,hks_lua_State *hksState)",
	"",
	"{",
	"  CSChrEventModule *pCVar1;",
	"  undefined4 uVar2;",
	"",
	"  pCVar1 = (*chrInsHolder)->modules->event;",
	"  uVar2 = FUN_1406832a0(pCVar1,envId);",
	"  return uVar2;",
	"}",
])

hand_written_c := concat("\n", [
	"#include <stdio.h>",
	"",
	"int main(void) {",
	"  printf(\"hi\\n\");",
	"  return 0;",
	"}",
])

test_allow_bash_decompile_redirect_into_scratchpad_c if {
	not denied(bash_event_in(repo, concat("", [
		"timeout 28 python3 scripts/ghidra/mcp_query.py getDecompiledCode --params '{\"address\":\"1406832a0\"}' 2>&1",
		" | python3 -c \"import json,sys; d=json.load(sys.stdin); r=d.get('result'); print(r)\" > ",
		session_scratchpad, "/f1406832a0.c; wc -l ", session_scratchpad, "/f1406832a0.c",
	])))
}

test_allow_bash_cd_repo_then_decompile_redirect_into_scratchpad_c if {
	not denied(bash_event_in(repo, concat("", [
		"cd /home/banon/projects/er-mods-rs && python3 scripts/ghidra/mcp_query.py getDecompiledCode",
		" --params '{\"address\":\"1404275e0\"}' | jq -r .result > ",
		session_scratchpad, "/chractionflag-1162.c; wc -l ", session_scratchpad, "/chractionflag-1162.c",
	])))
}

test_allow_bash_cd_scratchpad_then_relative_decompile_c if {
	not denied(bash_event_in(repo, concat("", [
		"cd ", session_scratchpad,
		" && jq -r .result hksenv-1162.json > hksenv-1162.c && wc -l hksenv-1162.c",
		" && python3 -I -c \"\nimport re\nlines=open('hksenv-1162.c').read().split(chr(10))\n\" | head -150",
	])))
}

test_allow_write_decompiler_output_c_into_scratchpad if {
	not denied(write_with("Write", concat("", [session_scratchpad, "/f1406832a0.c"]), ghidra_dump))
}

test_allow_bash_heredoc_of_decompiler_text_into_tmp_c if {
	not denied(bash_event_in(repo, concat("", [
		"cat > ", session_scratchpad, "/f1406832a0.c <<'EOF'\n", ghidra_dump, "\nEOF",
	])))
}

# A `<<` inside a quoted program is an operator in that program, not a heredoc.
test_allow_bash_shift_operator_inside_quotes_is_not_a_heredoc if {
	not denied(bash_event_in(repo, concat("", [
		"jq -r .flags dump.json | python3 -c \"import sys; print(int(sys.stdin.read()) << 3)\" > ",
		session_scratchpad, "/flags.c",
	])))
}

# A copy puts no agent-written bytes there: the source stays versioned where it was.
test_allow_bash_copy_versioned_cpp_into_tmp if {
	not denied(bash_event_in(repo, "cp scripts/vs-patch/dxa_lite.cpp /tmp/dxa-build/dxa_lite.cpp"))
}

test_deny_write_hand_written_c_into_tmp if {
	denied(write_with("Write", concat("", [session_scratchpad, "/probe.c"]), hand_written_c))
}

test_deny_write_c_without_decompiler_markers if {
	denied(claude_write_event("/tmp/probe.c"))
}

test_deny_edit_hand_written_c_into_tmp if {
	denied(edit_with(concat("", [session_scratchpad, "/f1406832a0.c"]), hand_written_c))
}

test_deny_bash_heredoc_hand_written_c_into_tmp if {
	denied(bash_event_in(repo, concat("", [
		"cat > ", session_scratchpad, "/probe.c <<'EOF'\n", hand_written_c, "\nEOF",
	])))
}

test_deny_bash_cd_scratchpad_heredoc_hand_written_relative_c if {
	denied(bash_event_in(repo, concat("", [
		"cd ", session_scratchpad, " && cat > probe.c <<'EOF'\n", hand_written_c, "\nEOF",
	])))
}

test_deny_bash_cd_scratchpad_echo_relative_c if {
	denied(bash_event_in(repo, concat("", [
		"cd ", session_scratchpad, " && echo 'int main(void) { return 0; }' > probe.c",
	])))
}

# ---------------------------------------------------------------------------
# A relative target after `cd` gets the verdict its absolute spelling gets.
# ---------------------------------------------------------------------------

test_deny_bash_cd_scratchpad_heredoc_relative_py if {
	denied(bash_event_in(repo, concat("", [
		"cd ", session_scratchpad, " && cat > probe.py <<'EOF'\nprint(1)\nEOF",
	])))
}

test_deny_bash_cd_quoted_scratchpad_heredoc_relative_py if {
	denied(bash_event_in(repo, concat("", [
		"cd \"", session_scratchpad, "\" && cat > probe.py <<'EOF'\nprint(1)\nEOF",
	])))
}

test_deny_bash_cd_then_relative_cd_redirect_py if {
	denied(bash_event_in(repo, concat("", [
		"cd ", session_scratchpad, " && mkdir -p hks && cd hks && echo 'print(1)' > walk.py",
	])))
}

test_deny_bash_cd_scratchpad_relative_tee_py if {
	denied(bash_event_in(repo, concat("", [
		"cd ", session_scratchpad, "; echo 'print(1)' | tee probe.py",
	])))
}

test_deny_bash_cd_scratchpad_relative_curl_output_js if {
	denied(bash_event_in(repo, concat("", [
		"cd ", session_scratchpad, " && curl -sL https://example.invalid/x.js -o x.js",
	])))
}

test_deny_bash_cd_scratchpad_relative_cp_destination_py if {
	denied(bash_event_in(repo, concat("", [
		"cd ", session_scratchpad, " && cp ", repo, "/scripts/probe.py probe.py",
	])))
}

test_deny_bash_wget_output_document_long_flag_tmp_js if {
	denied(bash_event("wget --output-document=/tmp/planner/x.js https://example.invalid/x.js"))
}

test_deny_bash_subshell_cd_scratchpad_relative_py if {
	denied(bash_event_in(repo, concat("", [
		"(cd ", session_scratchpad, " && echo 'print(1)' > a.py)",
	])))
}

test_deny_bash_subshell_redirect_tmp_py if {
	denied(bash_event("(echo 'print(1)' > /tmp/a.py)"))
}

# A relative target resolves against the hook's cwd when no `cd` came before it.
test_allow_bash_relative_script_write_in_repo_cwd if {
	not denied(bash_event_in(repo, "cat > scripts/probe.py <<'EOF'\nprint(1)\nEOF"))
}

test_allow_bash_relative_write_before_cd_into_scratchpad if {
	not denied(bash_event_in(repo, concat("", [
		"echo 'print(1)' > scripts/probe.py && cd ", session_scratchpad, " && ls",
	])))
}

# `-O` is python's optimize flag here, not a download's output file.
test_allow_bash_python_optimize_flag_runs_tmp_script if {
	not denied(bash_event_in(repo, "python3 -O /tmp/x.py"))
}

test_allow_bash_cd_scratchpad_python_optimize_flag_runs_relative_script if {
	not denied(bash_event_in(repo, concat("", ["cd ", session_scratchpad, " && python3 -O x.py"])))
}

test_allow_bash_cd_scratchpad_git_show_relative_target if {
	not denied(bash_event_in(repo, concat("", [
		"cd ", session_scratchpad, " && git show main:.cupcake/tests/teardown_must_relaunch_test.rego > old_test.rego",
	])))
}

test_deny_bash_cd_scratchpad_git_show_then_run if {
	denied(bash_event_in(repo, concat("", [
		"cd ", session_scratchpad, " && git show main:scripts/x.py > x.py && python3 x.py",
	])))
}

# The `scratchpad/hks/*.py` refusal of this session stands: a Python file authored
# in /tmp is what this guard exists to stop, nested directory or not.
test_deny_write_python_into_scratchpad_subdir if {
	denied(write_with("Write", concat("", [session_scratchpad, "/hks/spell_charge_windows.py"]), "print(1)"))
}

# --- a /tmp target through a variable or in quotes (2026-11-19) ---------------
# The first is the command a subagent ran that day, which authored `repro.sh` into the
# scratchpad and was allowed while a Write of the same file was refused. Each of these was
# allowed, measured with `opa eval`: `$S/...` was neither absolute nor a plain relative path,
# and a quoted target captured nothing.
tmp_writes_through_a_variable := [
	concat("", ["cd /home/banon/projects/er-mods-rs && S=", session_scratchpad, "; rm -f $S/repro.py; cat > $S/repro.sh <<'EOF'\n#!/usr/bin/env bash\nrepo=$1\nEOF"]),
	concat("", ["S=", session_scratchpad, "; cat > $S/repro.sh <<'EOF'\necho hi\nEOF"]),
	concat("", ["S=", session_scratchpad, " && cat > ${S}/repro.sh <<'EOF'\necho hi\nEOF"]),
	concat("", ["S=", session_scratchpad, "; cat > \"$S/repro.sh\" <<'EOF'\necho hi\nEOF"]),
	concat("", ["S=\"", session_scratchpad, "\"; cat > $S/repro.sh <<'EOF'\necho hi\nEOF"]),
	concat("", ["export S=", session_scratchpad, "; cat > $S/repro.py <<'EOF'\nprint(1)\nEOF"]),
	concat("", ["S=", session_scratchpad, "; echo 'echo hi' | tee $S/repro.sh"]),
	concat("", ["S=", session_scratchpad, "; cp scripts/a.py $S/b.py"]),
	concat("", ["S=", session_scratchpad, "; cp scripts/a.py \"$S/b.py\""]),
	concat("", ["S=", session_scratchpad, "/; printf 'x' > $S/a.sh"]),
	concat("", ["cat > \"", session_scratchpad, "/repro.sh\" <<'EOF'\necho hi\nEOF"]),
	concat("", ["echo hi > '", session_scratchpad, "/repro.sh'"]),
	# A relative value is resolved from the directory the write runs in.
	concat("", ["D=probe; cd ", session_scratchpad, " && cat > $D/x.sh <<'EOF'\necho hi\nEOF"]),
]

test_deny_tmp_script_write_through_a_variable_or_quotes if {
	every command in tmp_writes_through_a_variable {
		denied(bash_event_in(repo, command))
	}
}

# Data through the same variable is still allowed, and so is a script into the repo.
test_allow_tmp_data_and_repo_script_through_a_variable if {
	not denied(bash_event_in(repo, concat("", ["S=", session_scratchpad, "; cat > $S/out.json <<'EOF'\n{}\nEOF"])))
	not denied(bash_event_in(repo, concat("", ["S=", session_scratchpad, "; cat $S/a.sh > out.txt"])))
	not denied(bash_event_in(repo, "S=/home/banon/projects/er-mods-rs/target/x; cat > $S/repro.sh <<'EOF'\necho hi\nEOF"))
	not denied(bash_event_in(repo, "S=target/x; cat > $S/repro.sh <<'EOF'\necho hi\nEOF"))
}

# Only the last binding before the write counts, and only a literal one: a later binding out of
# /tmp, a binding that is a prefix to the writing command itself (bash expands `$S` before it
# takes effect), a value the shell expands, or a name last bound by `read`/`for` leaves the
# write allowed, as before.
test_allow_unresolved_variable_targets if {
	not denied(bash_event_in(repo, concat("", ["S=", session_scratchpad, "; S=target; cat > $S/a.sh < /dev/null"])))
	not denied(bash_event_in(repo, concat("", ["S=", session_scratchpad, " cat > $S/a.sh < /dev/null"])))
	not denied(bash_event_in(repo, "S=$HOME/x; cat > $S/a.sh < /dev/null"))
	not denied(bash_event_in(repo, concat("", ["S=", session_scratchpad, "; read S; cat > $S/a.sh < /dev/null"])))
	not denied(bash_event_in(repo, concat("", ["S=", session_scratchpad, "; cat > $S/$N.sh < /dev/null"])))
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

test_deny_bash_redirect_through_braced_quoted_variable_naming_whole_file if {
	denied(bash_event("export F=\"/tmp/work/probe.py\"; echo 'print(1)' > \"${F}\""))
}

# A whole-file variable bound to a data file is still a data file.
test_allow_bash_redirect_through_variable_naming_data_file if {
	not denied(bash_event("F=/tmp/work/out.log; cargo test -p er-gfx > $F 2>&1"))
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
