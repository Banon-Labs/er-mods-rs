# METADATA
# scope: package
# title: Block Python File Writes From Bash (use Edit/Write instead)
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-BASH-NO-PYTHON-FILE-WRITE
#   description: >-
#     Hard block on editing files by running a python program from the Bash tool.
#     The shape this exists to stop is a long `python3 - <<'PY' ... open(path,
#     'w').write(s) ... PY` heredoc that rewrites a source file: the edit never
#     appears as a reviewable diff in the transcript, a single bad `replace`
#     silently no-ops or corrupts the file, and one call can burn many minutes
#     composing a program whose only job was an edit the Edit tool makes in one
#     step. User directive 2026-09-16, after ~25 minutes of one file being
#     rewritten this way: "We NEED a hook to stop you from using python to write
#     massive files."
#     Use the Edit tool for a change to an existing file and the Write tool for a
#     new one. Both are diffed, both fail loudly when the anchor does not match,
#     and neither costs a turn to compose.
#     Shell redirection is NOT blocked -- `cmd > file` and a plain heredoc into a
#     file are visible in the command itself. Only python doing the writing is.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.bash_no_python_file_write

import rego.v1

command := object.get(input.tool_input, "command", "")

# A python interpreter as a command token: at command start or after a shell
# separator, optionally path-prefixed (`/usr/bin/python3`, `./python`), and
# terminated by a non-identifier char. `uv run ... python3` is caught by the
# same token match because `python3` follows whitespace there too.
python_token_pattern := "(^|[[:space:];|&('\"`])/?([[:alnum:]_.-]+/)*python[0-9.]*($|[^[:alnum:]_])"

invokes_python if {
	regex.match(python_token_pattern, command)
}

# The write itself. Each pattern names a way python mutates a file on disk.
#
# `open(..., 'w')` and friends are matched on the MODE argument rather than on
# `open(` alone, so a read (`open(path)`, `open(path, 'rb')`) is untouched --
# reading a file to answer a question is the thing this guard wants to stay
# cheap. `pathlib.Path.write_text` / `write_bytes` carry the mode in the method
# name, and `shutil.copy`/`move` write without ever naming a mode.
# A python file MODE, and nothing else that happens to be a quoted string.
#
# The mode alphabet is exactly `rwxab+t`, and a real mode is at most four of them.
# Pinning both is what keeps ordinary keyword arguments out: `errors='replace'`
# begins with `r`, and a looser `['"][rwxa][a-z+]*['"]` reads it as the mode `r`
# plus `eplace` and denies a pure read. Measured 2026-09-16, one command after this
# guard landed -- `open(p, encoding='utf8', errors='replace').read()` was refused,
# which is a guard blocking the exact work it exempts.
write_mode_pattern := `open\([^)]*['"][wxa][rwxab+t]{0,3}['"]`

# `r+`, and the two spellings that put the binary flag on either side of it
# (`rb+`, `r+b`). Update mode reads as well as writes, so it belongs here.
read_plus_pattern := `open\([^)]*['"]r[bt]*\+[bt]*['"]`

write_pattern contains pattern if {
	some pattern in [
		write_mode_pattern,
		read_plus_pattern,
		`open\([^)]*mode[[:space:]]*=[[:space:]]*['"][wxa][rwxab+t]{0,3}['"]`,
		`\.write_text\(`,
		`\.write_bytes\(`,
		`\.writelines\(`,
		`shutil\.(copy|copy2|copyfile|move)\(`,
		`os\.(remove|unlink|rename|replace|truncate)\(`,
	]
}

writes_a_file if {
	some pattern in write_pattern
	regex.match(pattern, command)
}

python_file_write_detected if {
	invokes_python
	writes_a_file
	not runs_a_committed_script
}

# ---------------------------------------------------------------------------
# Committed-script exemption.
#
# `python3 scripts/<name>.py ...` runs a reviewed file that lives in the repo.
# Whatever it writes was written once, reviewed once, and is re-runnable -- the
# opposite of an inline program composed for one edit. The exemption is for the
# INVOCATION only: the command must not also carry an inline program, so a
# `python3 scripts/foo.py` followed by a `python3 -c '...open(p,"w")...'` in the
# same command stays denied.
#
# Fail-closed shape: a heredoc (`<<`), a `-c` inline program, or a `-` stdin
# program anywhere in the command disqualifies it, because those are exactly the
# forms that carry an inline program.
# ---------------------------------------------------------------------------
runs_a_committed_script if {
	not contains(command, "<<")
	not regex.match(`(^|[[:space:]])-c($|[[:space:]])`, command)
	not regex.match(python_token_pattern_followed_by_stdin, command)
	regex.match(`python[0-9.]*[[:space:]]+[^[:space:]-][^[:space:]]*\.py($|[[:space:]])`, command)
}

python_token_pattern_followed_by_stdin := "python[0-9.]*[[:space:]]+-($|[[:space:]])"

block_reason := "🧁 Cupcake blocked a python file write from Bash. Editing a file by running a python program hides the change: it never shows up as a reviewable diff, a mismatched `replace` anchor silently no-ops, and composing the program costs a turn that the edit itself does not. Use the Edit tool to change an existing file (it fails loudly when the anchor does not match) and the Write tool to create one. Reading files in python is untouched, and so is shell redirection -- `cmd > file` and a plain heredoc into a file are visible in the command itself. A committed `python3 scripts/<name>.py` is also allowed; an inline program (`-c`, `<<HEREDOC`, `python3 -`) is not. User directive 2026-09-16: \"We NEED a hook to stop you from using python to write massive files.\""

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	python_file_write_detected

	decision := {
		"rule_id": "ER-EFFECTS-BASH-NO-PYTHON-FILE-WRITE",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nSource: ", command]),
	}
}
