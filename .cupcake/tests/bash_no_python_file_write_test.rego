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
