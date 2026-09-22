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

import data.cupcake.system.commands

command := object.get(input.tool_input, "command", "")

# The texts this command actually hands to a shell, quoted operand spans and heredoc
# bodies anchor-neutralised, so command position survives and quoted prose has none.
executed_texts := commands.input_executed_texts

# A python interpreter in COMMAND POSITION, not merely a `python` somewhere in the text.
#
# The pattern this replaces anchored on "start, or after whitespace or a quote", which is
# the thing every quoted argument in the world satisfies. Measured 2026-09-21 (bd
# er-effects-rs-ak3q): `bd remember --key ... "measured with python3 scripts/er-fd-trace.py
# --tsv ..."` was refused as an inline python file write. The invoked binary was `bd`, the
# python was a quotation inside the memory being recorded, and the way past the guard was
# to reword the memory -- degrading the record rather than the command.
#
# A second spelling of the same defect came with it and is also closed here: the ellipsis.
# `runs_a_committed_script` disqualified the WHOLE command on `..` appearing anywhere,
# meaning the `...` in that prose broke the committed-script exemption, and so did a real
# `python3 scripts/er-fd-trace.py --out ../x.tsv`, whose `..` is in an operand and not in
# the script path at all. The test belongs to the path.
python_word(word) if startswith(word, "python")

python_word(word) if contains(word, "/python")

python_invocation(words, index) if {
	python_word(words[index])
	commands.word_in_command_slot(words, index)
}

invokes_python if {
	some text in executed_texts
	words := commands.command_slot_words(text)
	some index, _ in words
	python_invocation(words, index)
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

# A `.py` FILE invocation whose path is not a committed, reviewed script
# carries no `open(..., 'w')` text of its own on THIS command line -- that
# text lives inside the file being run, not in the command that runs it -- so
# `writes_a_file` alone can never see it. Guard gap measured 2026-09-17: a
# throwaway file written to the session scratchpad with a `cat > ... <<'EOF'`
# heredoc (allowed on purpose -- shell redirection stays visible in the
# command) and then run as a SEPARATE `python3 /tmp/.../patch.py` call sailed
# through, because that second call is a one-line script invocation with no
# inline write text to match. Cupcake evaluates each Bash call independently,
# so the only way to catch call two is to distrust script-file execution
# itself unless the path is one this repo has already reviewed and committed.
python_file_write_detected if {
	invokes_python
	runs_a_python_script_file
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

# The `.py` path a python invocation is handed: the first word after it that is not a
# flag. Read as tokens rather than as a pattern over the whole command, which is what
# lets the committed-script test below ask about the PATH instead of about the command.
#
# The pattern this replaces had a trailing-context bug of its own, fixed 2026-09-17 and
# preserved here by construction: a separator may abut the path with no space in front of
# it (`python3 /tmp/x.py; echo "exit=$?"`), and `command_slot_words` splits the separator
# off as a word of its own, so the path is the same token either way.
script_operand(words, index) := words[j] if {
	python_invocation(words, index)
	some j
	j > index
	j < count(words)
	endswith(words[j], ".py")
	count([p |
		some p, _ in words
		p > index
		p < j
		not startswith(words[p], "-")
	]) == 0
}

runs_a_python_script_file if {
	not contains(command, "<<")
	not regex.match(`(^|[[:space:]])-c($|[[:space:]])`, command)
	not regex.match(python_token_pattern_followed_by_stdin, command)
	some text in executed_texts
	words := commands.command_slot_words(text)
	some index, _ in words
	script_operand(words, index) != ""
}

# The exemption itself, narrowed to match what the block message has always
# promised ("A committed `python3 scripts/<name>.py` is also allowed"): the
# path must be a repo-relative file under the repo's tracked `scripts/`
# directory tree -- `scripts/<name>.py`, `scripts/<subdir>/<name>.py` -- and
# nothing else. NOT an absolute path (`/tmp/...`, `/home/...`), NOT a
# home-relative path (`~/...`), and NOT a `..` escape out of the tree: none of
# those name a file this repo has committed or reviewed, no matter how closely
# they resemble `scripts/<name>.py` in shape. The old regex checked only the
# `.py` suffix and the absence of a leading dash, so `/tmp/.../patch.py` and
# `~/scratch/patch.py` both satisfied it -- the bypass an earlier rewrite closed.
committed_script_path(path) if {
	startswith(path, "scripts/")
	not contains(path, "..")
}

committed_script_path(path) if {
	startswith(path, "./scripts/")
	not contains(path, "..")
}

runs_a_committed_script if {
	runs_a_python_script_file
	every text in executed_texts {
		every_script_operand_is_committed(text)
	}
}

# Every `.py` this command hands to python is a committed one. Asked over all of them
# rather than "some", because the exemption is for the invocation and a command that runs
# `scripts/foo.py` and `/tmp/patch.py` has not earned it.
every_script_operand_is_committed(text) if {
	words := commands.command_slot_words(text)
	count([index |
		some index, _ in words
		path := script_operand(words, index)
		not committed_script_path(path)
	]) == 0
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
