package cupcake.policies.claude.bash_no_python_file_write_test

import data.cupcake.policies.claude.bash_no_python_file_write
import rego.v1

bash(cmd) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd},
}

# --- the shape the user asked to be stopped -------------------------------

test_heredoc_rewriting_a_source_file_is_denied if {
	cmd := "python3 - <<'PY'\np='crates/er-invasion-warp/src/lib.rs'\ns=open(p,encoding='utf8').read()\ns=s.replace('a','b')\nopen(p,'w',encoding='utf8').write(s)\nPY"
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

test_inline_dash_c_write_is_denied if {
	cmd := `python3 -c "open('notes.md','w').write('x')"`
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

test_pathlib_write_text_is_denied if {
	cmd := "python3 - <<'PY'\nimport pathlib\npathlib.Path('a.rs').write_text('hi')\nPY"
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

test_uv_run_inline_write_is_denied if {
	cmd := "uv run --with capstone python3 - <<'PY'\nopen('out.txt','w').write('x')\nPY"
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

test_shutil_copy_from_inline_python_is_denied if {
	cmd := `python3 -c "import shutil; shutil.copy('a','b')"`
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

# --- reading stays cheap, which is the point ------------------------------

test_reading_a_file_is_allowed if {
	cmd := "python3 -c \"import re,glob; [print(f) for f in glob.glob('src/**/*.rs',recursive=True) if re.search('x',open(f,encoding='utf8').read())]\""
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

test_binary_read_is_allowed if {
	cmd := `python3 -c "d=open('image.bin','rb').read(); print(len(d))"`
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

test_open_with_no_mode_is_allowed if {
	cmd := `python3 -c "print(open('a.json').read())"`
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

# --- forms that stay visible in the command itself ------------------------

test_shell_redirection_is_allowed if {
	cmd := "cat > /tmp/x.txt <<'EOF'\nhello\nEOF"
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

test_committed_script_is_allowed if {
	cmd := "python3 scripts/er-dump-ersc-image.py --out vendor-archive/seamless/x.bin"
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

test_committed_script_under_uv_is_allowed if {
	cmd := "uv run --with capstone python3 scripts/ersc-xrefs.py --to 0xa96e0"
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

# A committed script named on the same line as an inline program is still an
# inline program, so the exemption must not rescue it.
test_committed_script_plus_inline_write_is_denied if {
	cmd := "python3 scripts/ok.py && python3 -c \"open('a','w').write('x')\""
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

# --- the committed-script exemption is scoped to scripts/, not any .py path -
#
# Guard gap measured 2026-09-17: the old exemption regex checked only the
# `.py` suffix, so a script run from outside the repo's tracked `scripts/`
# tree was waved through exactly like a reviewed file. Neither denied command
# below carries an inline `open(..., 'w')` on its own line -- that text lives
# inside the file being run -- which is precisely why the fix has to distrust
# the invocation itself rather than widen the text scan.

test_tmp_scratchpad_script_is_denied if {
	cmd := "python3 /tmp/claude-1000/-home-banon-projects-er-mods-rs/scratchpad/patch_actions.py"
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

# A separator abutting the path is still the same invocation.
#
# Measured against the live policy 2026-09-17, immediately after the location
# fix above landed: the bare form denied and this one did not, because the
# trailing context was whitespace-or-end and `;` is neither. The suffix is one
# the harness actively encourages -- another guard here asks for a command's
# exit code to be read as `; echo "exit=$?"` -- so this was the shape most
# likely to be typed, not an exotic one.
test_tmp_script_with_abutting_separator_is_denied if {
	every cmd in [
		`python3 /tmp/x/patch.py; echo "exit=$?"`,
		"python3 /tmp/x/patch.py&& echo hi",
		"python3 /tmp/x/patch.py|tee log",
		"(python3 /tmp/x/patch.py)",
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
	}
}

# The same abutting separator on a committed script must still be exempt, or
# widening the trailing class above would deny every legitimate scoped gate.
test_committed_script_with_abutting_separator_is_allowed if {
	every cmd in [
		`python3 scripts/check-comment-caps.py; echo "exit=$?"`,
		"python3 scripts/er-teardown.py > /dev/null 2>&1; python3 scripts/er-run-branch.py",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
	}
}

test_home_scratch_script_is_denied if {
	cmd := "python3 ~/scratch/patch.py"
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

test_absolute_scripts_lookalike_path_is_denied if {
	# Starts with "scripts/" only after an absolute prefix -- still not
	# repo-relative, so it must not borrow the exemption.
	cmd := "python3 /home/banon/scripts/patch.py"
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

test_dotdot_escape_from_scripts_is_denied if {
	cmd := "python3 scripts/../../../tmp/patch.py"
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

test_uv_run_tmp_script_is_denied if {
	cmd := "uv run --with capstone python3 /tmp/scratch/patch.py"
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

test_relative_scripts_dir_script_is_allowed if {
	cmd := "python3 ./scripts/foo.py"
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

test_nested_scripts_subdir_script_is_allowed if {
	cmd := "python3 scripts/ghidra/mcp_query.py getContext"
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

# --- this repo's own ABSOLUTE path is the same committed file ---------------
#
# Added 2026-09-21. Admitting only the repo-relative spelling made the guard a
# teacher of it, and the spelling it taught then reached a user-facing message,
# where the user's shell is not in the repo root and the command names nothing.
# The exemption is about WHICH FILE runs, so both spellings of the same file
# resolve the same way; the lookalike test above still denies an absolute path
# that lands outside this repo.
test_repo_absolute_path_script_is_allowed if {
	every cmd in [
		"python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py",
		"python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=band-tables-measured",
		"python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getContext",
		"uv run --with capstone python3 /home/banon/projects/er-mods-rs/scripts/locate-ersc-entry-points.py",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
	}
}

# A worktree checkout of this repo is the same tree under a different prefix.
test_worktree_absolute_path_script_is_allowed if {
	cmd := "python3 /home/banon/projects/er-mods-rs/.worktrees/lab/scripts/check-comment-caps.py"
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

# ...but the repo name is the whole of what admits it, so a scratch tree that
# merely contains a `scripts/` directory stays denied.
test_absolute_scratch_scripts_dir_is_denied if {
	every cmd in [
		"python3 /tmp/claude-1000/scratchpad/scripts/patch.py",
		"python3 /home/banon/other-repo/scripts/patch.py",
		# The session scratchpad's last component ENDS in the repo name without
		# being it, so the repo name has to be matched as a whole component.
		"python3 /tmp/claude-1000/-home-banon-projects-er-mods-rs/scripts/patch.py",
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
	}
}

# --- the same checkout spelled relative to the repo root (2026-10-13) --------
#
# The absolute spelling above was admitted and this one was refused. The first command is
# the one that was refused in production: a committed test run from a PR worktree, reached
# through `$W`.
worktree_user_cmd := "cd /home/banon/projects/er-mods-rs && S=/tmp/x; W=.worktrees; ( python3 $W/pr-hook-shim/scripts/test-cupcake-hook-shim.py > $S/hs.log 2>&1; echo done )"

test_worktree_relative_path_script_is_allowed if {
	every cmd in [
		"python3 .worktrees/pr-hook-shim/scripts/test-cupcake-hook-shim.py",
		"python3 ./.worktrees/lab/scripts/ghidra/mcp_query.py getContext",
		"uv run --with frida python3 .worktrees/lab/scripts/er-frida-watch.py --agent x.js",
		"(python3 .worktrees/lab/scripts/check-comment-caps.py)",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
	}
}

test_worktree_script_through_a_variable_is_allowed if {
	every cmd in [
		worktree_user_cmd,
		"W=.worktrees; python3 ${W}/lab/scripts/check-comment-caps.py x.rs",
		"W=.worktrees/; python3 $W/lab/scripts/check-comment-caps.py",
		"R=/home/banon/projects/er-mods-rs; python3 $R/.worktrees/lab/scripts/er-teardown.py --status",
		"W=.worktrees S=/tmp/x; python3 $W/lab/scripts/check-stages.py --selftest > $S/out 2>&1",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
	}
}

# `..` anywhere, a checkout name that is not exactly one component, and a path that leaves
# `scripts/` all keep the refusal, written out or through a variable.
test_worktree_lookalike_or_traversal_is_denied if {
	every cmd in [
		"python3 .worktrees/../../tmp/scripts/x.py",
		"python3 .worktrees/lab/../scripts/x.py",
		"python3 .worktrees/lab/scripts/../../../tmp/x.py",
		"python3 .worktrees/scripts/x.py",
		"python3 .worktrees//scripts/x.py",
		"python3 .worktrees/./scripts/x.py",
		"python3 .worktrees/a/b/scripts/x.py",
		"python3 .worktrees/lab/tools/x.py",
		"python3 foo/.worktrees/lab/scripts/x.py",
		"python3 /tmp/.worktrees/lab/scripts/x.py",
		"W=.worktrees; python3 $W/../../tmp/scripts/x.py",
		"W=.worktrees/..; python3 $W/tmp/scripts/x.py",
		"W=/tmp; python3 $W/.worktrees/lab/scripts/x.py",
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
	}
}

# A variable the policy cannot pin to one literal is not resolved, so the operand is judged
# as `$W/...` and refused the way it was before.
test_worktree_variable_that_cannot_be_pinned_down_is_denied if {
	every cmd in [
		# never assigned in this command
		"python3 $W/lab/scripts/x.py",
		# assigned twice
		"W=.worktrees; W=/tmp; python3 $W/lab/scripts/x.py",
		# appended to
		"W=.worktrees; W+=/../../tmp; python3 $W/lab/scripts/x.py",
		# bound by a builtin rather than an assignment
		"W=.worktrees; read W; python3 $W/lab/scripts/x.py",
		# a prefix assignment: bash expands `$W` before it takes effect
		"W=.worktrees python3 $W/lab/scripts/x.py",
		# assigned after the invocation
		"python3 $W/lab/scripts/x.py; W=.worktrees; echo",
		# assigned in a subshell, which does not bind it here
		"( W=.worktrees ); python3 $W/lab/scripts/x.py",
		"(W=.worktrees); python3 $W/lab/scripts/x.py",
		# a value that is not a literal
		"W=$HOME/x; python3 $W/lab/scripts/x.py",
		"W=~/x; python3 $W/lab/scripts/x.py",
		"W='/tmp'; python3 $W/lab/scripts/x.py",
		# a second, unassigned variable after the first
		"W=.worktrees; python3 $W/$N/scripts/x.py",
		# a parameter expansion with a modifier
		"W=.worktrees; python3 ${W:-/tmp}/lab/scripts/x.py",
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
	}
}

# The exemption is for the invocation, so an inline program beside it is still refused.
test_worktree_script_plus_inline_write_is_denied if {
	cmd := "python3 .worktrees/lab/scripts/ok.py && python3 -c \"open('a','w').write('x')\""
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

# --- not a python command at all ------------------------------------------

test_non_python_command_is_allowed if {
	count(bash_no_python_file_write.deny) == 0 with input as bash("cargo test -p er-invasion-warp")
}

test_other_tools_are_untouched if {
	count(bash_no_python_file_write.deny) == 0 with input as {
		"hook_event_name": "PreToolUse",
		"tool_name": "Edit",
		"tool_input": {"file_path": "a.rs", "old_string": "x", "new_string": "y"},
	}
}

# --- keyword arguments are not modes -------------------------------------
#
# Added 2026-09-16, one command after the guard landed: it refused a pure read
# because `errors='replace'` begins with `r`, which a loose mode regex read as a
# python mode. A guard that blocks the work it exempts is a guard gap.

test_errors_replace_is_not_a_write if {
	cmd := `python3 -c "print(open(p, encoding='utf8', errors='replace').read())"`
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

test_encoding_keyword_is_not_a_write if {
	cmd := `python3 -c "open('a.log', encoding='utf8').read()"`
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

test_errors_replace_alongside_a_real_write_is_denied if {
	cmd := `python3 -c "s=open(p,encoding='utf8',errors='replace').read(); open(p,'w').write(s)"`
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

test_read_plus_is_a_write if {
	cmd := `python3 -c "f=open('a.bin','r+b')"`
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

test_append_mode_is_a_write if {
	cmd := `python3 -c "open('a.log','a').write('x')"`
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

# --- a payload that QUOTES a python command is not one ---------------------
#
# Measured in production 2026-09-21. The invoked binary is `bd` and `git`; the
# python is a quotation inside the memory or the commit message being recorded. The
# old anchor was "start, or after whitespace or a quote", which every quoted
# argument satisfies, and the ellipsis in the prose then broke the committed-script
# exemption on top of it.

test_bd_memory_quoting_a_python_command_is_allowed if {
	cmd := `bd remember --key trace-tsv "measured with python3 scripts/er-fd-trace.py --tsv ... which printed 12 rows"`
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

test_commit_message_quoting_a_python_write_is_allowed if {
	cmd := `git commit -m "docs: record that python3 -c \"open(p,'w').write(s)\" is the shape we refuse"`
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

test_issue_body_quoting_a_python_script_is_allowed if {
	cmd := `bd create --description "the repro is python3 /tmp/patch.py, which writes crates/ sources"`
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

# --- `..` belongs to the path, not to the command --------------------------

test_committed_script_with_a_dotdot_operand_is_allowed if {
	cmd := "python3 scripts/er-fd-trace.py --out ../trace.tsv"
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

test_committed_script_with_an_ellipsis_in_a_quoted_argument_is_allowed if {
	cmd := `python3 scripts/er-frida-evidence.py --record-telemetry --line "gfx_swap: armed ... released"`
	count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
}

# ...while a `..` escape in the SCRIPT PATH is still not a committed script.
test_dotdot_escape_in_the_script_path_is_denied if {
	cmd := "python3 scripts/../../elsewhere/patch.py"
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

# A real invocation inside a shell wrapper payload is still caught: the payload is a
# text of its own, where the python IS in command position.
test_python_write_inside_a_shell_wrapper_payload_is_denied if {
	cmd := `bash -c "python3 -c \"open('a','w').write('x')\""`
	count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
}

# --- a scratch script that only reads (2026-09-29) --------------------------
#
# Both commands below were refused in production although the scripts they ran write
# nothing. The policy cannot read a file, so the verdict on its contents comes from the
# `python_script_writes` signal (`.cupcake/signals/python_script_writes.sh`, judgement in
# `scripts/cupcake_python_script_writes.py`, whose `--selftest` scans real temp files for
# the same shapes). `opa test` runs no signals, so each case supplies the line the signal
# would print. The scratchpad path is a placeholder: the policy compares it only against
# the signal's text and never touches the filesystem.

scratch := "/tmp/claude-1000/-home-banon-projects-er-mods-rs/SESSION/scratchpad"

bash_with_signal(cmd, signal) := {
	"hook_event_name": "PreToolUse",
	"tool_name": "Bash",
	"tool_input": {"command": cmd},
	"signals": {"python_script_writes": signal},
}

analyse_cmd := concat("", ["timeout 29 python3 ", scratch, "/analyse.py ", scratch, " 2>&1 | cut -c1-260"])

test_read_only_scratch_script_is_allowed if {
	signal := concat("", ["READONLY ", scratch, "/analyse.py\n"])
	count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(analyse_cmd, signal)
}

# The same verdict in the `{output: ...}` shape cupcake can hand a signal back in.
test_read_only_verdict_in_output_shape_is_allowed if {
	signal := {"output": concat("", ["READONLY ", scratch, "/analyse.py"])}
	count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(analyse_cmd, signal)
}

# The second refused command, with `S` assigned in the same command so the signal can
# resolve it. Its committed scripts are admitted by path as before; the scratch one by the
# signal, matched with the quotes `command_slot_words` keeps stripped off.
selftest_and_sim_cmd := concat("", [
	"S=", scratch, "; python3 scripts/er-mechanics-ashes.py --selftest > $S/out 2>&1; ",
	"timeout 25 python3 \"$S/sim.py\" | head -9; python3 scripts/check-comment-caps.py scripts/x.py",
])

test_committed_scripts_plus_read_only_scratch_script_is_allowed if {
	count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(selftest_and_sim_cmd, "READONLY $S/sim.py")
}

# Everything short of an exact READONLY line for the operand keeps the refusal. `every`,
# not `some`: a `some` over a list passes when any one element denies.
test_scratch_script_without_a_read_only_verdict_is_denied if {
	every signal in [
		concat("", ["WRITES ", scratch, "/analyse.py open() mode 'w'"]),
		concat("", ["UNKNOWN ", scratch, "/analyse.py unresolved variable"]),
		"",
		"READONLY /some/other/analyse.py",
		concat("", ["READONLY ", scratch, "/analyse.pyc"]),
		{"error": "signal exited 1"},
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(analyse_cmd, signal)
	}
}

test_scratch_script_with_no_signal_at_all_is_denied if {
	count(bash_no_python_file_write.deny) == 1 with input as bash(analyse_cmd)
}

test_unresolved_variable_scratch_script_is_denied if {
	cmd := "timeout 25 python3 $S/sim.py | head -9"
	count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(cmd, "UNKNOWN $S/sim.py unresolved variable")
}

# The quoted `"$S/sim.py"` above is inspected, not skipped: without its verdict the same
# command is refused. Before 2026-10-13 the quote hid the operand and this allowed.
test_quoted_scratch_script_without_a_verdict_is_denied if {
	count(bash_no_python_file_write.deny) == 1 with input as bash(selftest_and_sim_cmd)
}

# One vetted script does not carry another that was not vetted.
test_read_only_script_beside_an_unvetted_one_is_denied if {
	cmd := concat("", ["python3 ", scratch, "/analyse.py; python3 ", scratch, "/patch.py"])
	signal := concat("", ["READONLY ", scratch, "/analyse.py\nWRITES ", scratch, "/patch.py open() mode 'w'"])
	count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(cmd, signal)
}

# A vetted script does not excuse an inline program in the same command.
test_read_only_script_plus_inline_write_is_denied if {
	cmd := concat("", ["python3 ", scratch, "/analyse.py && python3 -c \"open('a','w').write('x')\""])
	signal := concat("", ["READONLY ", scratch, "/analyse.py"])
	count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(cmd, signal)
}

# --- a read-only script beside a `sed -n` or a `$(...)` (2026-11-19/20) -------
#
# Both were refused in production while running a script the signal reads as READONLY on its
# own: the signal marked every command naming `sed`, and every command holding `$(`. It now
# judges sed by its arguments and reads a substitution's words like any others, so it prints
# READONLY for these, and these cases pin that the policy admits on that line. The `sed -i` and
# `s///w` shapes still get UNKNOWN from the signal (its `--selftest` checks both), and the
# policy keeps the refusal on that line and on no line at all.

stamp_script := "/home/banon/projects/er-mods-rs/.worktrees/r21/target/v28-mem/stamp_log.py"

sed_pipe_cmd := concat("", [
	"cd /home/banon/projects/er-mods-rs/.worktrees/r21 && python3 ", stamp_script,
	` target/v30-spells/wrap.log 400 /home/banon/.cache/er-build-planner/attack-similarity-140-160-6ac1ef7f362bdefb.only-sets '2026-11-19 18:52:05'`,
	` | grep -E 'attack sets|since the start' | sed -n '1,3p;8,10p;$p' | cut -c1-190`,
])

substitution_cmd := concat("", [
	"cd /home/banon/projects/er-mods-rs/.worktrees/r21/target/v30-spells && n=$(wc -l < wrap.log); { python3 ",
	stamp_script, ` wrap.log $n /home/banon/.cache/er-build-planner/x.only-sets '2026-11-19 18:52:05';`,
	" tail -n +$((n+1)) -F --pid=2514771 wrap.log | gawk -f ",
	"/home/banon/projects/er-mods-rs/.worktrees/r21/target/v28-mem/stamp_live.awk; } > wrap.stamped.log",
])

test_read_only_script_beside_sed_n_or_a_substitution_is_allowed if {
	every cmd in [sed_pipe_cmd, substitution_cmd] {
		count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(cmd, concat("", ["READONLY ", stamp_script]))
	}
}

test_read_only_script_beside_sed_n_or_a_substitution_without_a_verdict_is_denied if {
	every cmd in [sed_pipe_cmd, substitution_cmd] {
		count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
		count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(cmd, concat("", ["UNKNOWN ", stamp_script, " command substitution"]))
	}
}

test_script_beside_a_sed_that_writes_is_denied if {
	every pair in [
		[concat("", ["sed -i 's/a/b/' ", stamp_script, "; python3 ", stamp_script]), "sed -i writes the file in place"],
		[concat("", ["python3 ", stamp_script, " | sed 's/a/b/w out'"]), "sed s///w flag writes a file"],
	] {
		signal := concat("", ["UNKNOWN ", stamp_script, " a write-capable tool in the same command (", pair[1], ")"])
		count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(pair[0], signal)
		count(bash_no_python_file_write.deny) == 1 with input as bash(pair[0])
	}
}

# --- two ways a scratch script used to run uninspected (2026-10-13) ---------
#
# Every command in the two deny tests below was allowed by this policy before that date,
# measured. A quoted path failed the `.py` suffix test, and a `-c` or stdin `-` anywhere in
# the command switched off the script-file rule instead of only withholding the exemption.

test_quoted_scratch_script_is_denied if {
	every cmd in [
		`python3 "/tmp/patch.py"`,
		`python3 '/tmp/patch.py'`,
		`(python3 "/tmp/x/patch.py")`,
		`timeout 25 python3 "$S/patch.py" | head -9`,
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
	}
}

test_quoted_committed_script_is_allowed if {
	every cmd in [
		`python3 "scripts/check-comment-caps.py" x.rs`,
		`python3 '/home/banon/projects/er-mods-rs/scripts/er-teardown.py' --status`,
		`python3 ".worktrees/lab/scripts/check-stages.py" --selftest`,
		`W=.worktrees; python3 "$W/lab/scripts/check-stages.py" --selftest`,
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
	}
}

test_inline_program_marker_does_not_hide_a_scratch_script if {
	every cmd in [
		"wc -c f; python3 /tmp/patch.py",
		`bash -c "python3 /tmp/patch.py"`,
		`python3 -c "print(1)"; python3 /tmp/patch.py`,
		"cat x | python3 - ; python3 /tmp/patch.py",
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
	}
}

# The marker still only withholds the exemption: a committed script, or a vetted read-only
# one, beside a `-c` that writes nothing is allowed as before.
test_inline_program_marker_beside_admitted_scripts_is_allowed if {
	every cmd in [
		`python3 scripts/check-comment-caps.py x.rs; python3 -c "print(1)"`,
		`bash -c "python3 scripts/check-stages.py --selftest"`,
		"wc -c f; python3 scripts/check-comment-caps.py f",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
	}
	cmd := concat("", [`python3 -c "print(1)"; python3 "`, scratch, `/analyse.py"`])
	signal := concat("", ["READONLY ", scratch, "/analyse.py"])
	count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(cmd, signal)
}

# --- a committed test suite of the user's guard repos (2026-11-16) ------------
#
# Refused in production that day: the global cupcake guards' own test suite. It runs
# subprocesses and writes temp files, and it lives outside this repo's `scripts/`. The signal
# prints `GUARDTEST <operand>` only for a file under a guard repo's tests directory that is in
# that repo's HEAD (`scripts/cupcake_python_script_writes.py --selftest` checks that half
# against the real repo); each case here supplies the line it would print.

guard_suite := "/home/banon/.config/cupcake/tests/check_background_tails.py"

guard_suite_cmd := concat("", ["python3 ", guard_suite])

test_committed_guard_test_suite_is_allowed if {
	every cmd in [
		guard_suite_cmd,
		concat("", [guard_suite_cmd, " --replay /tmp/claude-1000/x/t.jsonl 2>&1 | tail -40"]),
		concat("", ["timeout 28 python3 \"", guard_suite, "\""]),
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(cmd, concat("", ["GUARDTEST ", guard_suite, "\n"]))
	}
}

test_guard_test_suite_without_a_guardtest_verdict_is_denied if {
	every signal in [
		"",
		concat("", ["WRITES ", guard_suite, " imports subprocess"]),
		concat("", ["UNKNOWN ", guard_suite, " a write-capable tool in the same command"]),
		"GUARDTEST /home/banon/.config/cupcake/tests/other.py",
		concat("", ["READONLY ", guard_suite, "x"]),
		{"error": "signal exited 1"},
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(guard_suite_cmd, signal)
	}
	count(bash_no_python_file_write.deny) == 1 with input as bash(guard_suite_cmd)
}

# The same suite committed in a `git worktree add` checkout of the guard repo (2026-11-26). The
# signal says which checkouts count (`--selftest` checks a real worktree, a worktree of another
# repo and an uncommitted file); the policy admits the operand its line names, relative or not.
guard_wt := "/home/banon/.config/cupcake-wt-no-cleanup-handoff"

test_worktree_guard_test_suite_is_allowed if {
	rel_cmd := concat("", ["cd ", guard_wt, " && git rev-parse HEAD && timeout 25 python3 tests/check_cleanup_handoff.py 2>&1 | tail -3"])
	count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(rel_cmd, "GUARDTEST tests/check_cleanup_handoff.py\n")
	abs_suite := concat("", [guard_wt, "/tests/check_cleanup_handoff.py"])
	abs_cmd := concat("", ["python3 ", abs_suite])
	count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(abs_cmd, concat("", ["GUARDTEST ", abs_suite, "\n"]))
}

test_worktree_guard_test_suite_without_a_guardtest_verdict_is_denied if {
	rel_cmd := concat("", ["cd ", guard_wt, " && timeout 25 python3 tests/check_cleanup_handoff.py"])
	every signal in [
		"",
		"WRITES tests/check_cleanup_handoff.py imports shutil",
		"UNKNOWN tests/check_cleanup_handoff.py a write-capable tool in the same command",
		"GUARDTEST tests/other.py",
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(rel_cmd, signal)
	}
}

# The admission is for that operand: an inline write, or a second unvetted script, beside it
# keeps the refusal.
test_guard_test_suite_beside_other_writes_is_denied if {
	signal := concat("", ["GUARDTEST ", guard_suite])
	every cmd in [
		concat("", [guard_suite_cmd, " && python3 -c \"open('a','w').write('x')\""]),
		concat("", [guard_suite_cmd, "; python3 /tmp/patch.py"]),
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(cmd, signal)
	}
}

# --- a python program run by name (2026-11-16) ---------------------------------
#
# `kitty-tail-bg --selftest` ran unchecked that day: a python program on PATH carries neither
# `python` nor `.py` on the command line. The signal resolves each word to a file and prints
# `EXEC-<verdict> TAB <word> TAB <real path> TAB <why>`; the policy refuses a WRITES or UNKNOWN
# verdict for a word in command position.

tail_bg := "/home/banon/.local/bin/kitty-tail-bg"

exec_line(kind, word, path, why) := concat("\t", [kind, word, path, why])

tail_bg_writes := exec_line("EXEC-WRITES", "kitty-tail-bg", tail_bg, "imports subprocess")

test_python_program_run_by_name_is_denied if {
	every cmd in [
		"kitty-tail-bg --selftest",
		"timeout 9 kitty-tail-bg --dry-run --title t /tmp/x.output",
		`bash -c "kitty-tail-bg --selftest"`,
		"(kitty-tail-bg --selftest)",
		"cd /tmp && kitty-tail-bg --selftest; echo done",
		"FOO=1 kitty-tail-bg --selftest",
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(cmd, tail_bg_writes)
	}
	decision := bash_no_python_file_write.deny with input as bash_with_signal("kitty-tail-bg --selftest", tail_bg_writes)
	some d in decision
	contains(d.reason, "`kitty-tail-bg` is /home/banon/.local/bin/kitty-tail-bg")

	# The refusal names the form that works here, the one the global Stop guard suggests.
	contains(d.reason, "/home/banon/projects/er-mods-rs/scripts/kitty-tail-task.py --split --title")
}

# The global background-task Stop guard's suggestion in this repo (2026-11-16) is admitted, with
# the signal's verdict for it as the real signal prints it.
test_global_tail_suggestion_is_allowed if {
	helper := "/home/banon/projects/er-mods-rs/scripts/kitty-tail-task.py"
	cmd := concat("", ["python3 ", helper, " --split --title 'tail: build (b1x2y3z4w)' --file /tmp/c/tasks/b1x2y3z4w.output --file /tmp/w/build.log"])
	count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(cmd, concat(" ", ["WRITES", helper, "imports subprocess"]))
}

# The same file by path, by a relative path, and handed to python without a `.py` suffix.
test_python_program_run_by_path_is_denied if {
	every pair in [
		[tail_bg, exec_line("EXEC-WRITES", tail_bg, tail_bg, "imports subprocess")],
		[concat("", ["python3 ", tail_bg, " --title t /tmp/x"]), exec_line("EXEC-WRITES", tail_bg, tail_bg, "imports subprocess")],
		["./patch", exec_line("EXEC-WRITES", "./patch", "/tmp/s/patch", "open() mode 'w'")],
		["./patch.py", exec_line("EXEC-UNKNOWN", "./patch.py", "-", "relative path, working directory unknown")],
		["S=/tmp/s; $S/patch", exec_line("EXEC-WRITES", "$S/patch", "/tmp/s/patch", "open() mode 'w'")],
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(pair[0], pair[1])
	}
}

test_read_only_or_guard_test_program_run_by_name_is_allowed if {
	every signal in [
		exec_line("EXEC-READONLY", "kitty-tail-bg", tail_bg, ""),
		exec_line("EXEC-GUARDTEST", "kitty-tail-bg", tail_bg, ""),
		"",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal("kitty-tail-bg --selftest", signal)
	}
}

# The signal names every word that resolves to a python program, wherever it stands; only one
# in command position is refused.
test_python_program_named_as_an_operand_is_allowed if {
	every cmd in [
		"echo kitty-tail-bg",
		"git log --oneline -- kitty-tail-bg",
		`bd remember --key k "run kitty-tail-bg --selftest after editing it"`,
		"cat /home/banon/.local/bin/kitty-tail-bg | head",
		"echo hi > kitty-tail-bg",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(cmd, tail_bg_writes)
	}
}

# This repo's committed scripts stay admitted when run directly, by either spelling or path.
test_committed_script_run_directly_is_allowed if {
	every pair in [
		["./scripts/kitty-tail-task.py --title t --file /tmp/x", exec_line("EXEC-WRITES", "./scripts/kitty-tail-task.py", "/home/banon/projects/er-mods-rs/scripts/kitty-tail-task.py", "imports subprocess")],
		["/home/banon/projects/er-mods-rs/scripts/er-teardown.py --status", exec_line("EXEC-WRITES", "/home/banon/projects/er-mods-rs/scripts/er-teardown.py", "/home/banon/projects/er-mods-rs/scripts/er-teardown.py", "imports subprocess")],
		["cd /x && .worktrees/lab/scripts/check-stages.py --selftest", exec_line("EXEC-UNKNOWN", ".worktrees/lab/scripts/check-stages.py", "-", "relative path, working directory unknown")],
		["W=.worktrees; $W/lab/scripts/check-stages.py", exec_line("EXEC-UNKNOWN", "$W/lab/scripts/check-stages.py", "-", "relative path, working directory unknown")],
		["er-tool --x", exec_line("EXEC-WRITES", "er-tool", "/home/banon/projects/er-mods-rs/scripts/er-tool", "imports subprocess")],
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(pair[0], pair[1])
	}
}

# A lookalike path is not admitted by the committed-script tests.
test_lookalike_program_path_is_denied if {
	every pair in [
		["/tmp/x/scripts/patch", exec_line("EXEC-WRITES", "/tmp/x/scripts/patch", "/tmp/x/scripts/patch", "open() mode 'w'")],
		["tool", exec_line("EXEC-WRITES", "tool", "/tmp/claude-1000/-home-banon-projects-er-mods-rs/scripts/tool", "open() mode 'w'")],
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(pair[0], pair[1])
	}
}

# One refusal per command: the `python3 <file>.py` rule answers first when both fire.
test_script_and_program_in_one_command_deny_once if {
	cmd := "python3 /tmp/patch.py; kitty-tail-bg --selftest"
	count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(cmd, tail_bg_writes)
}

# --- exec launchers in front of python (2026-11-19) ------------------------
#
# Measured 2026-11-19 with `opa eval` on this policy: `python3 /tmp/x/patch.py` was denied and
# each of these ran the same uncommitted script allowed, because the launcher was read as the
# program and `python3` as its operand. An agent session ran a scratch script with a computed
# `spec_from_file_location` path through `chrt --idle 0` that way. "EXEC LAUNCHERS" in
# `.cupcake/system/commands.rego` has the grammar.

launched_uncommitted := [
	"chrt --idle 0 python3 /tmp/x/patch.py",
	"chrt -i python3 /tmp/x/patch.py",
	"ionice -c 3 python3 /tmp/x/patch.py",
	"ionice -c idle python3 /tmp/x/patch.py",
	"taskset -c 0-3 python3 /tmp/x/patch.py",
	"taskset 0x3 python3 /tmp/x/patch.py",
	"timeout 30s python3 /tmp/x/patch.py",
	"timeout -s KILL 30 python3 /tmp/x/patch.py",
	"systemd-run --user --scope python3 /tmp/x/patch.py",
	"systemd-run --user --scope -u job python3 /tmp/x/patch.py",
	"systemd-run --user --scope -p MemoryMax=8G timeout 3h python3 /tmp/x/patch.py",
	"stdbuf -o L python3 /tmp/x/patch.py",
	"time python3 /tmp/x/patch.py",
	"/usr/bin/time -v python3 /tmp/x/patch.py",
	"sudo -u root python3 /tmp/x/patch.py",
	"flock -w 5 /tmp/lk python3 /tmp/x/patch.py",
	"numactl -C 0-3 python3 /tmp/x/patch.py",
	"strace -f -o /tmp/t python3 /tmp/x/patch.py",
	"cd /x && chrt --idle 0 python3 target/wf/splitcap/reach.py",
	"bash -c \"chrt --idle 0 python3 /tmp/x/patch.py\"",
	"python3 scripts/er-mechanics-resume.py --log /tmp/l -- python3 /tmp/x/patch.py",
	"python3 scripts/er-weapon-build.py --supervise --unit u -- chrt --idle 0 python3 /tmp/x/patch.py",
	"py-spy record -o /tmp/o -- python3 /tmp/x/patch.py",
]

test_launched_uncommitted_script_is_denied if {
	every cmd in launched_uncommitted {
		count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
	}
}

# The signal's verdict is what decides, wrapped or not: a script it found read-only is let
# through behind a launcher exactly as it is bare.
test_launched_read_only_script_is_allowed if {
	cmd := "chrt --idle 0 python3 /tmp/x/analyse.py"
	count(bash_no_python_file_write.deny) == 0 with input as bash_with_signal(cmd, "READONLY /tmp/x/analyse.py")
	count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(cmd, "WRITES /tmp/x/analyse.py importlib.spec_from_file_location outside this repo's scripts/")
}

test_launched_committed_script_is_allowed if {
	every cmd in [
		"chrt --idle 0 python3 scripts/er-mechanics-ashes.py --selftest",
		"taskset -c 0-3 python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py",
		"systemd-run --user --scope -p MemoryMax=8G timeout 3h python3 scripts/er-mechanics-attack-similarity.py --only x",
		"python3 scripts/er-mechanics-resume.py --log /tmp/l -- chrt --idle 0 python3 scripts/er-mechanics-attack-similarity.py --only x",
		"ionice -c idle nice -n 19 python3 .worktrees/r21/scripts/er-mechanics-ashes.py",
		"bash -c \"chrt --idle 0 python3 scripts/foo.py\"",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
	}
}

# Launchers do not turn quoted text or another program's operands into a python run.
test_python_named_as_an_operand_behind_a_launcher_is_allowed if {
	every cmd in [
		"chrt --idle 0 bd remember 'measured with python3 /tmp/x/patch.py'",
		"bash x.sh \"timeout 3 python3 /tmp/x/patch.py\"",
		"bash x.sh 'chrt -i 0 python3 /tmp/x/patch.py'",
		"git log -- python3 /tmp/x/patch.py",
		"time cargo test -p x",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
	}
}

# Absolute paths: only checkout hops may stand between `er-mods-rs` and `scripts`. A copy of
# the tool tree under `target/` (measured 2026-11-19: an agent's modified copy at
# `.worktrees/r21/target/wf/splitcap/scripts/` ran with `--selftest`) is not committed.
test_scripts_dir_copy_under_the_repo_is_not_committed if {
	every cmd in [
		"python3 /home/banon/projects/er-mods-rs/target/scratch/scripts/patch.py",
		"python3 /home/banon/projects/er-mods-rs/.worktrees/r21/target/wf/splitcap/scripts/er-mechanics-ashes.py --selftest",
		"chrt --idle 0 python3 /home/banon/projects/er-mods-rs/.worktrees/r21/target/wf/splitcap/scripts/er-mechanics-ashes.py --selftest",
		"python3 /home/banon/projects/er-mods-rs/crates/x/scripts/patch.py",
		"python3 /home/banon/projects/er-mods-rs/worktrees/x/scripts/patch.py",
		"python3 /home/banon/projects/er-mods-rs/.claude/x/scripts/patch.py",
		"python3 /home/banon/projects/er-mods-rs/.worktrees/scripts/patch.py",
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
	}
}

test_checkout_spellings_of_scripts_are_committed if {
	every cmd in [
		"python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py",
		"python3 /home/banon/projects/er-mods-rs/scripts/sub/x.py",
		"python3 /home/banon/projects/er-mods-rs/.worktrees/r21/scripts/er-mechanics-ashes.py",
		"python3 /home/banon/projects/er-mods-rs/.worktrees/a/.worktrees/b/scripts/x.py",
		"python3 /home/banon/projects/er-mods-rs/.claude/worktrees/agent-a1/scripts/x.py",
		"python3 /home/banon/projects/er-mods-rs/.claude/worktrees/agent-a1/.worktrees/committed-compiles/scripts/x.py",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
	}
}

# A python program run by name behind a launcher gets the same reading as bare.
test_launched_python_program_by_name_is_denied if {
	every cmd in ["chrt --idle 0 kitty-tail-bg --selftest", "taskset -c 0 kitty-tail-bg --selftest"] {
		count(bash_no_python_file_write.deny) == 1 with input as bash_with_signal(cmd, tail_bg_writes)
	}
}

# --- the script operand, found without a pass over the command (2026-11-19) ---
# `script_operand` reads the words after python up to the first that is not an option. Past
# the first eight words it falls back to the rest of the command, which finds the same word.
test_script_after_many_options_is_denied if {
	every cmd in [
		"python3 -u -B -O -E -s -S -I -P -q /tmp/x/patch.py",
		"python3 -u /tmp/x/patch.py",
		"ionice -c 3 python3 -u -B /tmp/x/patch.py; true",
	] {
		count(bash_no_python_file_write.deny) == 1 with input as bash(cmd)
	}
}

# A `.py` word after the script is the script's argument, not a second script, and a script
# after a non-option word (`-m mod`) is the module's.
test_operand_is_the_first_non_option_word if {
	every cmd in [
		"python3 scripts/er-teardown.py /tmp/x/patch.py",
		"python3 -m pytest /tmp/x/test_patch.py",
	] {
		count(bash_no_python_file_write.deny) == 0 with input as bash(cmd)
	}
}
