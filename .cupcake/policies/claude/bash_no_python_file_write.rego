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
write_pattern contains pattern if {
	some pattern in [
		# open(..., 'w'), "w", 'wb', 'a', 'r+', 'x' -- any mode that can write.
		`open\([^)]*['"][rwxa][a-z+]*\+?['"]`,
		`open\([^)]*mode[[:space:]]*=[[:space:]]*['"][rwxa][a-z+]*\+?['"]`,
		`\.write_text\(`,
		`\.write_bytes\(`,
		`\.writelines\(`,
		`shutil\.(copy|copy2|copyfile|move)\(`,
		`os\.(remove|unlink|rename|replace|truncate)\(`,
	]
}

# `open(...)` modes that only read. Listed so the mode regex above can stay one
# expression: it accepts `r` to catch `r+`, and this removes the read-only ones.
read_only_open_pattern := `open\([^)]*['"]r[b]?['"]`

writes_a_file if {
	some pattern in write_pattern
	regex.match(pattern, command)
	pattern != read_only_open_pattern
}

# A bare `open(path, 'r')` or `open(path, 'rb')` is a read. The mode pattern
# above matches it (it has to, to catch `r+`), so subtract it back out: a
# command whose ONLY write-shaped match is a read-only open is not a write.
only_reads if {
	regex.match(`open\([^)]*['"]r[b]?['"]`, command)
	not regex.match(`open\([^)]*['"][wxa]`, command)
	not regex.match(`open\([^)]*['"]r[b]?\+`, command)
	not regex.match(`\.write_text\(`, command)
	not regex.match(`\.write_bytes\(`, command)
	not regex.match(`\.writelines\(`, command)
	not regex.match(`shutil\.(copy|copy2|copyfile|move)\(`, command)
	not regex.match(`os\.(remove|unlink|rename|replace|truncate)\(`, command)
}

python_file_write_detected if {
	invokes_python
	writes_a_file
	not only_reads
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
