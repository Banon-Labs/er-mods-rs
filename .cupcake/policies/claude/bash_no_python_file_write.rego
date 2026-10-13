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
#     required_signals: ["python_script_writes"]
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
	not runs_only_admitted_scripts
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
#
# The inline-program markers below disqualify the exemption, and until 2026-10-13 they
# also switched this rule off, because it was written as "runs a script file and is not
# exempt" and `runs_a_python_script_file` carries them. So a `-c` or a stdin `-` anywhere
# in the command let any script through: `wc -c f; python3 /tmp/patch.py`,
# `bash -c "python3 /tmp/patch.py"` and `python3 -c "print(1)"; python3 /tmp/patch.py`
# were all allowed, measured on this policy. A marker is a reason to withhold the
# exemption, never a reason to stop looking at the scripts, so this rule now asks only
# whether a script operand is unadmitted. A heredoc still ends the check here: its body
# is read as words like any other text, and a body of prose that quotes a command would
# otherwise be refused as one.
python_file_write_detected if {
	invokes_python
	not contains(command, "<<")
	some text in executed_texts
	not every_script_operand_is_admitted(text)
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
#
# A closing subshell paren stays attached to the word (`(python3 /tmp/x/patch.py)` yields
# `/tmp/x/patch.py)`), so it is trimmed before the suffix test. Without that the subshell
# form named no script at all and ran uninspected; the test that listed it used `some`
# over its cases, which passed on the three that did deny (found 2026-09-29).
#
# Quotes are removed for the same reason, found 2026-10-13: `command_slot_words` keeps
# them, so `python3 "/tmp/patch.py"` ended in a quote, failed the suffix test, and ran
# uninspected exactly as the subshell form had. The path tests below and the signal's
# verdicts are both written against the unquoted path.
script_operand(words, index) := operand if {
	python_invocation(words, index)
	some j
	j > index
	j < count(words)
	operand := unquoted(trim_right(words[j], ")"))
	endswith(operand, ".py")
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
# promised ("A committed script under this repo's `scripts/` is also allowed"):
# the path must name a file under this repo's tracked `scripts/` directory tree
# -- `scripts/<name>.py`, `scripts/<subdir>/<name>.py` -- reached either
# repo-relatively or through this repo's own absolute path. Not some other
# absolute path (`/tmp/...`, `/home/banon/scripts/...`), not a home-relative
# path (`~/...`), and not a `..` escape out of the tree: none of those name a
# file this repo has committed or reviewed, no matter how closely they resemble
# `scripts/<name>.py` in shape. The old regex checked only the `.py` suffix and
# the absence of a leading dash, so `/tmp/.../patch.py` and `~/scratch/patch.py`
# both satisfied it -- the bypass an earlier rewrite closed.
committed_script_path(path) if repo_relative_script_path(trim_prefix(path, "./"))

repo_relative_script_path(path) if {
	startswith(path, "scripts/")
	not contains(path, "..")
}

# The same file in one of this repo's local worktree checkouts, spelled relative to the repo
# root: `.worktrees/<name>/scripts/<file>.py`. Added 2026-10-13. The absolute spelling of such
# a checkout was already admitted by the component rule below, and the relative one was not, so
# this command, which runs a committed test from a PR worktree, was refused as a python file
# write:
#
#   cd /home/banon/projects/er-mods-rs && S=/tmp/x; W=.worktrees; ( python3 $W/pr-hook-shim/scripts/test-cupcake-hook-shim.py > $S/hs.log 2>&1; echo done )
#
# `.worktrees/` is the gitignored directory AGENTS.md reserves for these checkouts, so what sits
# under `<name>/scripts/` is this repo's own `scripts/` tree on another branch. The `$W` in that
# command is resolved by `variable_resolved` further down.
#
# Narrow on purpose. The checkout name is exactly one path component, neither empty nor `.`,
# and `..` anywhere refuses the path, so `.worktrees/../../tmp/scripts/x.py` and
# `.worktrees/x/../scripts/x.py` stay where they were. Below the name, the path must pass the
# same test as a bare `scripts/` path, so the nesting rules are the two spellings' shared rules
# rather than a second copy of them.
committed_script_path(path) if {
	not contains(path, "..")
	segments := split(trim_prefix(path, "./"), "/")
	count(segments) > 3
	segments[0] == ".worktrees"
	segments[1] != ""
	segments[1] != "."
	repo_relative_script_path(concat("/", array.slice(segments, 2, count(segments))))
}

# This repo's own absolute spelling names the same committed file, added
# 2026-09-21. The narrowing above admitted the repo-relative form alone, and a
# guard that rewards one spelling teaches it: the agent then copies that
# spelling into a user-facing message, where the user's shell is in some other
# directory and `python3 scripts/er-teardown.py` names nothing -- measured on a
# teardown the user asked for twice and could not run. A path is admitted here
# because of the file it lands on, not because of how it was typed, so a
# `.../er-mods-rs/.../scripts/<name>.py` prefix joins the bare and `./` forms.
# User directive: "AGENTS.md and Cupcake block text need to be rectified."
#
# `er-mods-rs` has to be a whole path component, and the session scratchpad is
# why. It lives at `/tmp/claude-1000/-home-banon-projects-er-mods-rs/`, whose
# last component ends in the repo name without being it; a suffix test would
# have admitted every uncommitted scratch script filed under a `scripts/`
# subdirectory there, re-opening the hole the narrowing closed. Splitting the
# path on `/` is what makes the component test exact -- the same reason the
# rules above ask about the path rather than about the command, and it needs no
# regex in a rule that runs once per operand of every command.
#
# The intermediate components are unconstrained so that a worktree checkout of
# this repo (`.../er-mods-rs/.worktrees/<name>/scripts/...`) is the same tree
# under a different prefix, which is what it is.
committed_script_path(path) if {
	startswith(path, "/")
	not contains(path, "..")
	segments := split(path, "/")
	some repo_index
	segments[repo_index] == "er-mods-rs"
	some scripts_index
	scripts_index > repo_index
	segments[scripts_index] == "scripts"
	scripts_index < count(segments) - 1
}

runs_only_admitted_scripts if {
	runs_a_python_script_file
	every text in executed_texts {
		every_script_operand_is_admitted(text)
	}
}

# Every `.py` this command hands to python is admitted. Asked over all of them rather than
# "some", because the exemption is for the invocation and a command that runs
# `scripts/foo.py` and `/tmp/patch.py` has not earned it.
every_script_operand_is_admitted(text) if {
	words := commands.command_slot_words(text)
	count([index |
		some index, _ in words
		path := script_operand(words, index)
		not script_operand_admitted(words, index, path)
	]) == 0
}

script_operand_admitted(_, _, path) if committed_script_path(path)

script_operand_admitted(words, index, path) if committed_script_path(variable_resolved(words, index, path))

script_operand_admitted(_, _, path) if vetted_read_only(path)

# ---------------------------------------------------------------------------
# A committed script reached through a variable, added 2026-10-13.
#
# `W=.worktrees; python3 $W/<name>/scripts/<file>.py` names the same file as the literal
# spelling, and the path test above can only see `$W/...`. The `python_script_writes` signal
# already expands a variable like this one, but only to read the file and only for an absolute
# result, so a relative checkout path came back `UNKNOWN` and the refusal stood. The expansion
# here exists to run the path test on what the shell will actually hand python; whatever it
# yields still has to pass `committed_script_path`, so it admits nothing a literal would not.
#
# It resolves one shape and fails closed on the rest. The operand must begin `$NAME/` or
# `${NAME}/` with no other `$` after it. `NAME` must be bound exactly once in this text, by a
# statement made only of `NAME=value` words that comes before the python invocation, to a value
# with no expansion, quoting, glob or escape character in it. Any other word that could bind the
# name -- the bare name (`read W`, `for W in`), `W+=`, `W[...]`, a second `W=`, or a `W=` used as
# a prefix to a command, where bash expands `$W` before the assignment takes effect -- leaves the
# variable unresolved, and the operand is judged exactly as it was before this rule existed.
#
# Residue, stated rather than hidden: the shell's own word splitting is not modelled, so a
# command that also rebinds `IFS` can make bash split the expansion where this does not. Like
# the signal, this is a guard against a habit, and getting past it that way takes intent.
# ---------------------------------------------------------------------------

variable_resolved(words, index, path) := concat("/", [trim_right(value, "/"), rest]) if {
	reference := variable_reference(path)
	rest := reference[1]
	not contains(rest, "$")
	value := bound_literal(words, index, reference[0])
}

variable_reference(path) := [name, rest] if {
	startswith(path, "${")
	close := indexof(path, "}/")
	close > 2
	name := substring(path, 2, close - 2)
	rest := substring(path, close + 2, (count(path) - close) - 2)
}

variable_reference(path) := [name, rest] if {
	startswith(path, "$")
	not startswith(path, "${")
	slash := indexof(path, "/")
	slash > 1
	name := substring(path, 1, slash - 1)
	rest := substring(path, slash + 1, (count(path) - slash) - 1)
}

bound_literal(words, index, name) := value if {
	shell_name(name)
	prefix := concat("", [name, "="])
	bindings := [position |
		some position, word in words
		binds_name(word, name)
	]
	count(bindings) == 1
	position := bindings[0]
	position < index
	startswith(words[position], prefix)
	assignment_statement(words, position)
	value := substring(words[position], count(prefix), count(words[position]) - count(prefix))
	literal_value(value)
}

binds_name(word, name) if word == name

binds_name(word, name) if startswith(word, concat("", [name, "="]))

binds_name(word, name) if startswith(word, concat("", [name, "+="]))

binds_name(word, name) if startswith(word, concat("", [name, "["]))

# Every word of the statement holding `position` is an assignment, and a separator ends it.
assignment_statement(words, position) if {
	start := commands.command_slot_start(words, position)
	ends := [i |
		some i, word in words
		i > position
		word == commands.command_slot_separator
	]
	count(ends) > 0
	end := min(ends)
	count([i |
		some i, word in words
		i >= start
		i < end
		not assignment_word(word)
	]) == 0
}

assignment_word(word) if {
	equals := indexof(word, "=")
	equals > 0
	shell_name(substring(word, 0, equals))
}

shell_name(name) if {
	name != ""
	characters := split(name, "")
	not digit(characters[0])
	count([c |
		some c in characters
		not name_character(c)
	]) == 0
}

name_character(c) if contains("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789", c)

digit(c) if contains("0123456789", c)

literal_value(value) if {
	value != ""
	count([c |
		some c in ["$", "`", "\\", "'", `"`, "~", "*", "?", "[", "(", ")", "<", ">", "\t", "\n", "\r"]
		contains(value, c)
	]) == 0
}

# ---------------------------------------------------------------------------
# A scratch script that only reads, added 2026-09-29.
#
# The rule above refuses every uncommitted script file because the command line cannot
# show what the file does. That also refused this, which writes nothing:
#
#   timeout 29 python3 <scratchpad>/analyse.py <scratchpad> 2>&1 | cut -c1-260
#
# analyse.py loads two JSON files with `json.load(open(path))`, loads a committed tool as a
# library, and prints. Refusing it did not stop an edit; it only pushed a read-only
# analysis into a `python3 - <<'PY'` heredoc, which this guard allows when the body has no
# write in it -- the same program, now pasted instead of named.
#
# So the guard now asks what the file does. `.cupcake/signals/python_script_writes.sh`
# (judgement and selftest in `scripts/cupcake_python_script_writes.py`) reads each script
# the pending command names and prints `READONLY <operand>` when nothing in it writes. An
# operand is admitted here only on that exact line. Fail-closed by construction: `WRITES`,
# `UNKNOWN`, no output, a timeout (key absent), cupcake's failure record for a non-zero
# exit, and `opa test`, which supplies no signals, all leave the refusal as it was. The
# signal itself fails closed on a variable it cannot resolve from the same command, a
# relative path, a write-capable tool in the command, or the script path appearing twice.
#
# The operand is compared with its surrounding quotes removed, because
# `command_slot_words` keeps them (`"$S/sim.py"`) and the signal's shell tokenizer does not.
# ---------------------------------------------------------------------------

python_script_writes_signal := s if {
	s := input.signals.python_script_writes
	is_string(s)
} else := s if {
	s := input.signals.python_script_writes.output
	is_string(s)
} else := ""

vetted_read_only_operands contains operand if {
	some line in split(python_script_writes_signal, "\n")
	trimmed := trim(line, " \t\r")
	startswith(trimmed, "READONLY ")
	operand := trim_prefix(trimmed, "READONLY ")
	operand != ""
}

unquoted(word) := trim(word, "\"'")

vetted_read_only(path) if unquoted(path) in vetted_read_only_operands

python_token_pattern_followed_by_stdin := "python[0-9.]*[[:space:]]+-($|[[:space:]])"

block_reason := "🧁 Cupcake blocked a python file write from Bash. Editing a file by running a python program hides the change: it never shows up as a reviewable diff, a mismatched `replace` anchor silently no-ops, and composing the program costs a turn that the edit itself does not. Use the Edit tool to change an existing file (it fails loudly when the anchor does not match) and the Write tool to create one. Reading files in python is untouched, and so is shell redirection -- `cmd > file` and a plain heredoc into a file are visible in the command itself. A committed script under this repo's `scripts/` is also allowed, by either spelling -- `python3 scripts/<name>.py` or `python3 /home/banon/projects/er-mods-rs/scripts/<name>.py` -- and so is the same file in a local checkout, `.worktrees/<name>/scripts/<file>.py`, written out or reached through a `$VAR` assigned a literal by its own statement earlier in the same command. A script file anywhere else runs only when the `python_script_writes` signal reads it and finds no write: an absolute path (or `$VAR` assigned a literal earlier in the same command), no `open(..., 'w')`, `.write_text`, `shutil`, `subprocess`, `os.remove`-style call, and `importlib` pointed only at this repo's `scripts/`. An inline program (`-c`, `<<HEREDOC`, `python3 -`) is not. Write the absolute spelling in anything the user will read: their shell is not in the repo root and the relative form names nothing there. User directive 2026-09-16: \"We NEED a hook to stop you from using python to write massive files.\""

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
