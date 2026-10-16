# OPA unit tests for cap_parallel_fanout. Run with:
#   opa test .cupcake/system/commands.rego \
#            .cupcake/policies/claude/cap_parallel_fanout.rego \
#            .cupcake/tests/cap_parallel_fanout_test.rego
package cupcake.policies.claude.cap_parallel_fanout_test

import rego.v1

import data.cupcake.policies.claude.cap_parallel_fanout as guard

bash_event(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd, "timeout": 30000, "description": "test case"},
}

denied(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	"ER-EFFECTS-CAP-PARALLEL-FANOUT" in {d.rule_id | some d in denials}
}

allowed(cmd) if {
	denials := guard.deny with input as bash_event(cmd)
	count(denials) == 0
}

# The two runs measured on 2026-10-15, each 35-41 seconds before systemd-oomd killed the
# kitty terminal scope with everything in it.
test_deny_the_0955_run if {
	denied(`printf '%s\n' work/*/$k.ll | xargs -n 100 -P 15 python3 .../pos_slice.py`)
}

test_deny_the_1002_run if {
	denied("ls work/*/orig.ll | xargs -n 200 -P 8 python3 $P/pos_slice.py")
}

test_allow_four_workers if {
	allowed("ls work/*/orig.ll | xargs -n 200 -P 4 python3 $P/pos_slice.py")
}

test_allow_four_workers_attached if {
	allowed(`printf '%s\n' work/*/$k.ll | xargs -n 100 -P4 python3 .../pos_slice.py`)
}

test_allow_one_worker_long_option if {
	allowed("ls *.ll | xargs --max-procs=1 -n 1 python3 x.py")
}

# -P 0 is "as many as possible", which is no cap at all.
test_deny_p0_attached if {
	denied("ls *.ll | xargs -P0 -n 1 python3 x.py")
}

test_deny_p0_spaced if {
	denied("ls *.ll | xargs -n 1 -P 0 python3 x.py")
}

test_deny_max_procs_equals_16 if {
	denied("find . -name '*.ll' -print0 | xargs -0 --max-procs=16 -n 50 python3 x.py")
}

test_deny_max_procs_spaced if {
	denied("ls *.ll | xargs --max-procs 8 python3 x.py")
}

test_deny_max_procs_abbreviated if {
	denied("ls *.ll | xargs --max-p=8 python3 x.py")
}

test_deny_clustered_short_options if {
	denied("find . -print0 | xargs -0P8 -n 1 python3 x.py")
}

test_deny_computed_count if {
	denied(`ls *.ll | xargs -P "$(nproc)" -n 1 python3 x.py`)
}

test_deny_variable_count if {
	denied("ls *.ll | xargs -P $JOBS -n 1 python3 x.py")
}

test_deny_absolute_xargs if {
	denied("ls *.ll | /usr/bin/xargs -P 8 python3 x.py")
}

test_deny_behind_nice if {
	denied("ls *.ll | nice -n 19 xargs -P 8 python3 x.py")
}

test_deny_inside_bash_c if {
	denied(`bash -c "ls work/*/orig.ll | xargs -n 200 -P 8 python3 x.py"`)
}

test_deny_backgrounded_after_cd if {
	denied("cd /tmp/claude-1000/x && nohup sh -c 'ls | xargs -P 15 python3 x.py' > log 2>&1 &")
}

test_deny_inside_loop_body if {
	denied("for k in a b; do printf '%s\\n' work/*/$k.ll | xargs -n 100 -P 15 python3 x.py; done")
}

# xargs stops reading its own options at the command, so a flag of the command is not a
# worker count.
test_allow_grep_perl_regex_under_xargs if {
	allowed(`ls *.rs | xargs -n 20 grep -P 'fn \w+'`)
}

test_allow_xargs_without_p if {
	allowed("ls work/*/orig.ll | xargs -n 200 python3 $P/pos_slice.py")
}

test_allow_xargs_replace_without_p if {
	allowed(`ls *.ll | xargs -I {} sh -c 'python3 x.py {}'`)
}

# GNU parallel: -j/--jobs/-P/--max-procs, 0, percentages, and no option at all.
test_deny_parallel_j8 if {
	denied("parallel -j 8 python3 x.py ::: work/*/orig.ll")
}

test_deny_parallel_j0 if {
	denied("parallel -j0 python3 x.py ::: work/*/orig.ll")
}

test_deny_parallel_jobs_percent if {
	denied("parallel --jobs 50% python3 x.py ::: work/*/orig.ll")
}

test_deny_parallel_jobs_equals if {
	denied("parallel --jobs=16 python3 x.py ::: work/*/orig.ll")
}

test_deny_parallel_p_alias if {
	denied("ls *.ll | parallel -P 12 python3 x.py")
}

test_deny_parallel_relative_to_cores if {
	denied("parallel -j +0 python3 x.py ::: *.ll")
}

test_deny_parallel_default_one_per_core if {
	denied("parallel python3 x.py ::: work/*/orig.ll")
}

test_allow_parallel_j4 if {
	allowed("parallel -j 4 python3 x.py ::: work/*/orig.ll")
}

test_allow_parallel_options_before_jobs if {
	allowed("parallel --joblog /tmp/claude-1000/x/jobs.log -k -j2 python3 x.py ::: *.ll")
}

test_allow_parallel_version if {
	allowed("parallel --version")
}

test_allow_command_v_parallel if {
	allowed("command -v parallel xargs")
}

# The capped form: the kernel kills the job's own scope, not the terminal.
test_allow_systemd_run_memory_max_wrapper if {
	allowed(`systemd-run --user --scope -p MemoryMax=8G -- bash -c "ls work/*/orig.ll | xargs -n 200 -P 12 python3 /tmp/claude-1000/x/pos_slice.py"`)
}

test_allow_systemd_run_memory_max_wrapper_single_quoted if {
	allowed(`systemd-run --user --scope -p MemoryMax=8G -- bash -c 'printf "%s\n" work/*/$k.ll | xargs -n 100 -P 15 python3 x.py'`)
}

test_allow_systemd_run_memory_max_substitution_in_payload if {
	allowed(`systemd-run --user --scope -p MemoryMax=8G -- bash -c "ls $(pwd)/work | xargs -P 12 python3 x.py"`)
}

test_allow_systemd_run_property_equals if {
	allowed(`systemd-run --user --scope --property=MemoryMax=12G -- sh -c 'parallel -j 12 python3 x.py ::: *.ll'`)
}

test_allow_systemd_run_direct_xargs if {
	allowed("ls work/*/orig.ll | systemd-run --user --scope -p MemoryMax=8G xargs -n 200 -P 12 python3 x.py")
}

test_deny_systemd_run_without_memory_max if {
	denied(`systemd-run --user --scope -- bash -c "ls work/*/orig.ll | xargs -n 200 -P 12 python3 x.py"`)
}

test_deny_systemd_run_without_memory_max_direct if {
	denied("ls *.ll | systemd-run --user --scope -p CPUQuota=400% xargs -P 12 python3 x.py")
}

test_deny_systemd_run_memory_max_infinity if {
	denied(`systemd-run --user --scope -p MemoryMax=infinity -- bash -c "ls | xargs -P 12 python3 x.py"`)
}

test_deny_systemd_run_without_scope if {
	denied(`systemd-run --user -p MemoryMax=8G -- bash -c "ls | xargs -P 12 python3 x.py"`)
}

# The cap covers only its own statement: a fan-out after it runs in the terminal's scope.
test_deny_fanout_after_a_capped_statement if {
	denied("systemd-run --user --scope -p MemoryMax=8G -- true; ls *.ll | xargs -P 12 python3 x.py")
}

test_deny_capped_scope_piping_into_uncapped_fanout if {
	denied("systemd-run --user --scope -p MemoryMax=8G -- ls work | xargs -P 12 python3 x.py")
}

# Text that names the command without running it.
test_allow_git_commit_message if {
	allowed(`git commit -m "guard: ls work/*/orig.ll | xargs -n 200 -P 8 python3 pos_slice.py took the terminal down"`)
}

test_allow_git_commit_heredoc_message if {
	allowed("git commit -m \"$(cat <<'EOF'\nfeat(cupcake): cap parallel fan-out\n\nls work/*/orig.ll | xargs -n 200 -P 8 python3 $P/pos_slice.py\nparallel -j 16 x ::: y\nEOF\n)\"")
}

test_allow_bd_note if {
	allowed(`$HOME/.local/bin/bd remember --key oom-fanout "printf '%s\n' work/*/\$k.ll | xargs -n 100 -P 15 python3 pos_slice.py killed the kitty scope"`)
}

test_allow_quoted_grep_pattern if {
	allowed(`grep -rn 'xargs -P 16' scripts/`)
}

test_allow_quoted_grep_pattern_with_pipe if {
	allowed(`grep -rnE "\| xargs -P [0-9]+|parallel -j 8" scripts/ .cupcake/`)
}

test_allow_echo_of_the_command if {
	allowed(`echo "ls | xargs -P 16 python3 x.py"`)
}

test_allow_heredoc_script_body if {
	allowed("cat > /tmp/claude-1000/x/run.sh <<'EOF'\nls *.ll | xargs -P 16 python3 x.py\nEOF")
}

test_allow_unrelated_command if {
	allowed("cargo test -p er-soulsformats")
}
