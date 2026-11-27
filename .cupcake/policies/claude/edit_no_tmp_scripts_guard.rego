# METADATA
# scope: package
# title: No Authoring Scripts Into /tmp
# description: Author scripts/source in the repo (scripts/, scripts/ghidra/), not /tmp; /tmp is for artifacts only.
# custom:
#   severity: MEDIUM
#   id: ER-EFFECTS-NO-TMP-SCRIPTS-GUARD
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"]
package cupcake.policies.claude.edit_no_tmp_scripts_guard

import rego.v1

import data.cupcake.system.commands

# Source/script extensions that should live in the repo (reviewable, version-controlled,
# reusable across sessions) -- never authored into the volatile /tmp tree. DATA artifacts
# (.tsv, .log, .json, .bin, .txt, .csv ...) are deliberately NOT listed: writing those to
# /tmp is fine and intended. Each of these is run or loaded straight from where it sits, so one
# landing in /tmp is refused whoever produced its bytes.
script_exts := {
	".py", ".sh", ".bash", ".rs", ".java", ".rego", ".js", ".ts", ".tsx", ".jsx",
	".go", ".rb", ".pl", ".lua", ".ps1",
}

# C and C++ are judged by who wrote the bytes, because C is also what Ghidra's decompiler
# writes. Measured 2026-11-10: subagents saving `mcp_query.py getDecompiledCode` output to
# `<scratchpad>/f1406832a0.c` were refused, one renamed its dump `.decomp.txt` to get past, and
# `cd <scratchpad> && jq -r .result hksenv-1162.json > hksenv-1162.c` went through only because
# its target was relative. A dump is data that is read and never compiled; hand-written C is
# source and belongs in the repo, which does carry some (`scripts/vs-patch/dxa_lite.cpp`). So a
# C file in /tmp is refused when the agent typed bytes that are not decompiler output: the text
# of a Write or Edit, or a heredoc, `echo` or `printf` in Bash. A program's stdout, a download
# and a copy put no agent-written C there.
c_exts := {".c", ".cc", ".cpp", ".h", ".hpp"}

# Ghidra names what it cannot recover in shapes nobody types by hand (`uVar1`, `pCVar1`,
# `local_28`, `param_1`, `FUN_1406832a0`, `undefined4`) and heads a function it struggled with
# with `/* WARNING: ... */`. It never writes a preprocessor directive, which hand-written C
# almost always has. Substring tests rather than a regex: every distinct regex adds to the WASM
# regex cache cupcake 0.5.2 corrupts once too many are in play
# (bd cupcake-wasm-regex-cache-reuse-oob-2026-09-16).
decompiler_markers := {"/* WARNING: ", "Var1", "local_", "param_1", "FUN_", "DAT_", "LAB_", "undefined4", "undefined8"}

preprocessor_directives := {"#include", "#define", "#pragma", "#if"}

tool_input := object.get(input, "tool_input", {})
file_path := object.get(tool_input, "file_path", object.get(tool_input, "path", ""))
command := object.get(tool_input, "command", "")
lower_tool_name := lower(object.get(input, "tool_name", ""))
hook_cwd := object.get(input, "cwd", "")

# The text a Write, Edit, MultiEdit or NotebookEdit puts into the file.
authored_text := concat("\n", array.concat(
	[t |
		some key in ["content", "new_string", "new_source"]
		t := object.get(tool_input, key, "")
		is_string(t)
	],
	[t |
		some e in object.get(tool_input, "edits", [])
		is_object(e)
		t := object.get(e, "new_string", "")
		is_string(t)
	],
))

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	authoring_tool
	in_tmp(file_path)
	tool_authors_script

	decision := tmp_script_decision(file_path)
}

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	bash_tool
	not versioned_blob_extraction
	some path in tmp_written_script_paths

	decision := tmp_script_decision(path)
}

tmp_script_decision(path) := decision if {

	decision := {
		"rule_id": "ER-EFFECTS-NO-TMP-SCRIPTS-GUARD",
		"severity": "MEDIUM",
		"reason": concat("", [
			"🧁 Cupcake paused authoring a script into /tmp: ",
			path,
			"\n\nWhy this policy exists: /tmp is volatile (lost on reboot) and unversioned. Scripts authored there cannot be reviewed, reused across sessions, or shared with other agents. GhidraScripts repeatedly ended up stranded in /tmp/ghidra_scripts/.",
			"\n\nHappy path: author the script under the repo -- general helpers in `scripts/`, Ghidra postScripts in `scripts/ghidra/` -- and have IT write its data ARTIFACTS to /tmp or the session scratchpad if needed. (Data files like .tsv/.log/.json/.bin to /tmp are allowed, and so is C a decompiler wrote -- a program's output redirected into a .c file, or Ghidra's text with no `#include`; only source/scripts are blocked.)",
		]),
	}
}

tool_authors_script if is_script_path(file_path)

tool_authors_script if {
	is_c_path(file_path)
	not decompiler_output(authored_text)
}

authoring_tool if lower_tool_name == "write"

authoring_tool if lower_tool_name == "edit"

authoring_tool if lower_tool_name == "multiedit"

authoring_tool if lower_tool_name == "notebookedit"

authoring_tool if endswith(lower_tool_name, ".write")

authoring_tool if endswith(lower_tool_name, ".edit")

bash_tool if lower_tool_name == "bash"

bash_tool if lower_tool_name == "ctx_execute"

bash_tool if endswith(lower_tool_name, ".bash")

bash_tool if contains(lower_tool_name, "ctx_execute")

in_tmp(path) if {
	startswith(path, "/tmp/")
	not in_current_repo(path)
}

in_current_repo(path) if {
	hook_cwd != ""
	hook_cwd != "/"
	path == hook_cwd
}

in_current_repo(path) if {
	hook_cwd != ""
	hook_cwd != "/"
	startswith(path, concat("", [hook_cwd, "/"]))
}

is_script_path(path) if {
	some ext in script_exts
	endswith(path, ext)
}

is_c_path(path) if {
	some ext in c_exts
	endswith(path, ext)
}

decompiler_output(text) if {
	some marker in decompiler_markers
	contains(text, marker)
	not has_preprocessor_directive(text)
}

has_preprocessor_directive(text) if {
	some directive in preprocessor_directives
	contains(text, directive)
}

# WRITE INDICATORS, EACH ANCHORED TO THE PATH ITSELF.
#
# The Write/Edit/MultiEdit/NotebookEdit branch above needs no such test -- those tools ALWAYS
# author the file they name. Bash does not, and the previous form of this branch could not tell
# the difference: it asked only whether an interpreter-ish word appeared ANYWHERE in the command
# and a /tmp script path appeared ANYWHERE else. Two measured false positives (2026-08-25):
#
#   python3 - <<'EOF' ... open(p) ... glob('/tmp/planner/*.js') ... EOF
#       -> READS third-party js out of /tmp. Denied because "python3" and a "/tmp/*.js" both
#          occurred in the same command.
#   mkdir -p target/planner-bundle && cp -f /tmp/planner/*.js target/planner-bundle/ && ls ...
#       -> COPIES OUT of /tmp, no interpreter at all. Denied because the alternation had no word
#          boundaries and "bun" matched inside the substring "planner-bundle", after which `.*`
#          reached the /tmp path. Same trap for cat/"concatenate", node/"node_modules",
#          tee/"committee", echo/"echoes".
#
# So the interpreter alternation is gone entirely -- an interpreter NAME says nothing about
# direction -- and each pattern below binds a real write operator DIRECTLY to the path it writes,
# capturing that path in group 1. Every command word is anchored on both sides ((^|[\s;&|(]) before,
# required whitespace after) so a substring inside an unrelated word can never arm the rule.
#
# Group 1 is any path, absolute or relative (2026-11-10). It used to be `/tmp/...` only, so
# `cd <scratchpad> && cat > probe.py <<'EOF'` authored a script into /tmp unseen while the same
# write spelled absolutely was refused. A relative path is resolved against the directory the
# shell is in at that simple command (see base_dir) and then judged exactly like an absolute one.
# Its first character may not be `=`, so `x >= y.py` in a heredoc body is not a redirect, and may
# not be `-`, so `wget -O -` and `--output-document=` are not files. It never holds a parenthesis,
# so `(cd <scratchpad> && echo ... > a.py)` captures `a.py` and not `a.py)`.
#
# The path may open with a quote, which the group leaves out (2026-11-19). Measured that day with
# `opa eval`: `cat > "/tmp/claude-1000/x/repro.sh" <<'EOF'` and `cat > "$S/repro.sh"` were
# allowed, because the group's first character may not be a quote and nothing was captured.
# A `$NAME/` path is resolved by `resolve` below when the command binds NAME to a literal.
write_indicator_patterns := [
	# Shell redirection INTO the path: `> /tmp/x.py`, `>> /tmp/x.py`, `2>/tmp/x.py`, `&>/tmp/x.py`,
	# `>| /tmp/x.py`. This is also what covers a heredoc / `echo` / `printf` redirected into it.
	`>>?\|?\s*["']?([^\s"'\\;&|<>()=-][^\s"'\\;&|<>()]*)`,

	# `| tee /tmp/x.sh`, `tee -a /tmp/x.sh`.
	`(?:^|[\s;&|(])tee(?:\s+-{1,2}[^\s]+)*\s+["']?([^\s"'\\;&|<>()=-][^\s"'\\;&|<>()]*)`,

	# Download output flags: `curl -o /tmp/x.js`, `curl --output /tmp/x.js`, `wget -O /tmp/x.js`,
	# `wget --output-document=/tmp/x.js`. The flag must belong to a curl or wget in the same
	# simple command: on its own, `-O` is also python's optimize flag, and `python3 -O /tmp/x.py`
	# runs a script rather than writing one. Longest flag first, so `--output` does not stop short
	# of `--output-document`.
	`(?:^|[\s(])(?:curl|wget)\s(?:[^|]*?\s)?(?:--output-document|--output|-o|-O)[=\s]*["']?([^\s"'\\;&|<>()=-][^\s"'\\;&|<>()]*)`,

	# Copy/move/install DESTINATION. The path must be the LAST argument of its command
	# segment, which is what separates `cp a /tmp/x.py` (writes into /tmp) from
	# `cp /tmp/x.py a` and `cp -f /tmp/planner/*.js target/` (both read OUT of /tmp).
	`(?m)(?:^|[\s;&|(])(?:cp|mv|install|rsync|scp|ln)\s+(?:[^\s;&|<>]+\s+)+?["']?([^\s"'\\;&|<>()=-][^\s"'\\;&|<>()]*)["']?\s*(?:$|[;&|)])`,

	# In-language writes inside a heredoc / -c snippet: `open('/tmp/x.py', 'w')`. The mode must be
	# the second positional argument and must contain w/a/x, so a READ -- `open('/tmp/a.js')`, or
	# `open('/tmp/a.js', encoding='utf-8')` -- does not match.
	`open\(\s*['"]([^'"]+)['"]\s*,\s*['"][^'"]*[wax][^'"]*['"]`,

	# `Path('/tmp/x.py').write_text(...)` / `.write_bytes(...)`.
	`['"]([^'"]+)['"]\s*\)\s*\.\s*write`,
]

# The command, cut into the simple commands a shell runs one after another: split at `;`, `&&`,
# `||` and newlines that are not inside quotes. A single `|` or `&` stays inside its piece. An
# unpaired quote is read as an ordinary character, so a `don't` in a heredoc body cannot make
# text vanish. Leading `(`, `{`, and the separator's own leftovers are trimmed, so a `cd` opening
# a subshell is still seen as a `cd`.
segment_pattern := `(?:'[^']*'|"(?:[^"\\]|\\.)*"|[^'";&|\n]|\|[^|]|&[^&]|['"])+`

segments := [s |
	some m in regex.find_all_string_submatch_n(segment_pattern, command, -1)
	s := trim_space(trim_left(m[0], " \t&|({"))
]

# What a simple command is, read from its start, in one anchored pattern per piece:
#   group 1, 2: `cd`/`pushd`/`popd` and the rest of its words;
#   group 3:    `echo`/`printf`, which write text the agent typed;
#   group 4:    a heredoc or here-string operator outside quotes, also text the agent typed.
head_pattern := `^(?:(cd|pushd|popd)(?:[ \t]+(.*))?$|(echo|printf)[ \t]|(?:[^'"<]|'[^']*'|"(?:[^"\\]|\\.)*"|<[^<])*(<<))`

heads := [regex.find_all_string_submatch_n(head_pattern, s, 1) | some s in segments]

is_cd(i) if {
	some m in heads[i]
	m[1] != ""
}

# The literal directory a `cd`/`pushd` names: one word, no option but `-L`/`-P`/`--`, nothing
# the shell expands. Anything else (`cd`, `cd -`, `cd ~`, `cd "$D"`, `cd ..`, `popd`) leaves the
# directory unknown from there on, and a relative write in an unknown directory is not judged --
# the same verdict a write to `$D/x.py` gets, since neither names a path this policy can read.
cd_path(i) := p if {
	some m in heads[i]
	m[1] in {"cd", "pushd"}
	words := [w | some w in split(trim_space(m[2]), " "); w != ""]
	not "-" in words
	targets := [w | some w in words; not startswith(w, "-")]
	count(targets) == 1
	p := trim(targets[0], `"'`)
	plain_path(p)
}

plain_path(p) if {
	p != ""
	not startswith(p, "~")
	not startswith(p, "-")
	not contains(p, "$")
	not contains(p, "`")
	not contains(p, "..")
	not contains(p, "://")
}

relative_cd(i) if {
	p := cd_path(i)
	not startswith(p, "/")
}

# A `cd` to an absolute or unknown directory replaces the shell's directory outright; a
# relative one moves within it.
resetting_cd(i) if {
	is_cd(i)
	not relative_cd(i)
}

resets_before(i) := {j | some j, _ in segments; j < i; resetting_cd(j)}

relative_steps(from, to) := [p |
	some j, _ in segments
	j > from
	j < to
	relative_cd(j)
	p := trim_right(trim_prefix(cd_path(j), "./"), "/")
	p != "."
]

# The directory the shell is in when simple command i runs: the hook's cwd, or the last
# absolute `cd` before it, followed by every relative `cd` after that.
base_dir(i) := concat("/", array.concat([trim_right(hook_cwd, "/")], relative_steps(-1, i))) if {
	count(resets_before(i)) == 0
	hook_cwd != ""
}

base_dir(i) := concat("/", array.concat([trim_right(cd_path(r), "/")], relative_steps(r, i))) if {
	resets := resets_before(i)
	count(resets) > 0
	r := max(resets)
	startswith(cd_path(r), "/")
}

resolve(_, token) := token if startswith(token, "/")

resolve(i, token) := concat("/", [base_dir(i), trim_prefix(token, "./")]) if {
	not startswith(token, "/")
	plain_path(token)
}

# A path through a variable the command binds to a literal (2026-11-19). Measured that day with
# `opa eval`: `S=/tmp/claude-1000/<session>/scratchpad; cat > $S/repro.sh <<'EOF'` authored a
# script into the scratchpad and was allowed, while the same write spelled out was refused,
# because `$S/repro.sh` is neither absolute nor a plain relative path. A subagent wrote
# `repro.sh` there exactly so the same day.
#
# One shape is resolved, and every other one is judged as before, unresolved and allowed:
#   * the path opens `$NAME/` or `${NAME}/` and holds no other `$`;
#   * the last of the simple commands before this one that binds NAME -- has a word that is
#     NAME, `NAME=...`, `NAME+=...` or `NAME[...` -- is an assignment statement: every word
#     `NAME=value` style, `export` or `readonly` allowed in front;
#   * NAME's value there is a literal, or a literal in one pair of quotes.
# A relative value is resolved from the directory that write runs in, as a relative path is.
# The last binding is the one in force at the write, so `S=scripts; S=/tmp/w; > $S/x.py` is
# judged as `/tmp/w/x.py` and `S=/tmp/w; S=scripts; > $S/x.py` as `scripts/x.py` (main's
# 2026-11-07 tests of the same escape). A `read S` or `for S in` as the last binder is not an
# assignment statement, so it still leaves the path unresolved.
# Residue, measured the same day: `S=$(mktemp -d); cat > $S/x.sh` is not resolved.
resolve(i, token) := path if {
	reference := commands.variable_reference(token)
	not contains(reference[1], "$")
	value := bound_value(i, reference[0])
	joined := concat("/", [trim_right(value, "/"), reference[1]])
	path := resolved_value_path(i, joined)
}

# A variable naming the whole file, `$F` or `${F}`, under the same binding rules: main's
# 2026-11-07 suite refused `F=/tmp/probe.sh && printf ... > $F`, and the 2026-11-19 resolver
# let it through because it judged a target's extension before resolving it and `$F` has none.
# Its extension is judged on the bound value instead (tmp_written_script_paths).
resolve(i, token) := path if {
	name := whole_variable_name(token)
	value := bound_value(i, name)
	path := resolved_value_path(i, value)
}

# NAME for a token that is exactly `$NAME` or `${NAME}`, undefined otherwise. Token arithmetic,
# not a regex, for the WASM regex cache reason at decompiler_markers; the name test is the one
# commands.assignment_word applies to the NAME of `NAME=`.
whole_variable_name(token) := name if {
	startswith(token, "${")
	endswith(token, "}")
	name := substring(token, 2, count(token) - 3)
	commands.assignment_word(concat("", [name, "="]))
}

whole_variable_name(token) := name if {
	startswith(token, "$")
	not startswith(token, "${")
	name := substring(token, 1, -1)
	commands.assignment_word(concat("", [name, "="]))
}

resolved_value_path(_, joined) := joined if startswith(joined, "/")

resolved_value_path(i, joined) := concat("/", [base_dir(i), trim_prefix(joined, "./")]) if {
	not startswith(joined, "/")
	plain_path(joined)
}

# The literal NAME holds at simple command i: a directory for `$NAME/rest`, a whole path for
# `$NAME`.
bound_value(i, name) := value if {
	bindings := [j |
		some j in variable_bindings[name]
		j < i
	]
	count(bindings) > 0
	values := [v |
		some word in statement_assignments(segments[max(bindings)])
		startswith(word, concat("", [name, "="]))
		v := literal_or_quoted(substring(word, count(name) + 1, -1))
	]
	count(values) == 1
	value := values[0]
}

segment_words(segment) := [w |
	some w in split(replace(segment, "\t", " "), " ")
	w != ""
]

segment_binds(segment, name) if {
	# A substring test before the split, which every simple command before the write pays.
	contains(segment, name)
	some word in segment_words(segment)
	binds_name(word, name)
}

binds_name(word, name) if word == name

binds_name(word, name) if startswith(word, concat("", [name, "="]))

binds_name(word, name) if startswith(word, concat("", [name, "+="]))

binds_name(word, name) if startswith(word, concat("", [name, "["]))

# The words of an assignment statement after an `export` or `readonly`, or undefined when a
# word of it is not an assignment.
statement_assignments(segment) := assignments if {
	words := segment_words(segment)
	count(words) > 0
	assignments := array.slice(words, declaration_words(words[0]), count(words))
	count(assignments) > 0
	count([w |
		some w in assignments
		not commands.assignment_word(w)
	]) == 0
}

declaration_words(first) := 1 if first in {"export", "readonly"}

declaration_words(first) := 0 if not first in {"export", "readonly"}

literal_or_quoted(value) := value if commands.literal_shell_value(value)

literal_or_quoted(value) := inner if {
	count(value) > 2
	some q in {"\"", "'"}
	startswith(value, q)
	endswith(value, q)
	inner := substring(value, 1, count(value) - 2)
	commands.literal_shell_value(inner)
}

# A heredoc, `echo` or `printf` carries bytes the agent typed. They are still decompiler output
# when they read like it, the same test the Write branch applies to its content.
agent_typed_c(i) if {
	some m in heads[i]
	m[3] != ""
	not decompiler_output(command)
}

agent_typed_c(i) if {
	some m in heads[i]
	m[4] != ""
	not decompiler_output(command)
}

tmp_written_script_paths contains path if {
	some capture in captured_targets
	i := capture[0]
	path := resolve(i, capture[1])
	in_tmp(path)
	bash_authors_script(path, i)
}

# `[i, target]` for every write target of a source file in simple command i. A rule rather than
# a body of the one above, so it is computed once and `variable_bindings` can read it.
captured_targets contains [i, m[1]] if {
	# NOT regex.find_n: Cupcake's WASM runtime has no host implementation for it, so the call
	# returned undefined and this entire Bash-authoring branch silently never fired (2026-08-22).
	# regex.find_all_string_submatch_n is compiled into the WASM module itself, and m[1] is the
	# path captured by the write-indicator pattern's single group.
	some i, segment in segments
	some pattern in write_indicator_patterns
	some m in regex.find_all_string_submatch_n(pattern, segment, -1)
	# The extension first: resolving a path never changes it, and a data file is not judged.
	# A whole-variable target has no extension until it is resolved, so it is kept here and its
	# bound value is judged in tmp_written_script_paths.
	judged_target(m[1])
}

judged_target(token) if source_extension(token)

judged_target(token) if whole_variable_name(token)

# The simple commands that bind each variable a target opens with, read once per name. Asked
# once per target instead, 200 `echo a > $S/f<n>.sh;` statements (4.1 KB) aborted the hook's
# 100MB wasm module (measured 2026-11-19 with `cupcake eval --wasm-max-memory 100MB`).
#
# One complete object rather than a partial one keyed by name, which the wasm module did not
# keep between lookups: with `variable_bindings[name] := ...` the same 4.1 KB command still
# aborted (measured the same day).
variable_bindings := {name: positions |
	some name in target_variable_names
	positions := [j |
		some j, segment in segments
		segment_binds(segment, name)
	]
}

target_variable_names := {name |
	some capture in captured_targets
	name := target_variable_name(capture[1])
}

# The two shapes are disjoint: variable_reference needs a `/` after the name, and a whole
# variable's name is identifier characters only.
target_variable_name(token) := commands.variable_reference(token)[0]

target_variable_name(token) := whole_variable_name(token)

source_extension(token) if is_script_path(token)

source_extension(token) if is_c_path(token)

bash_authors_script(path, _) if is_script_path(path)

bash_authors_script(path, i) if {
	is_c_path(path)
	agent_typed_c(i)
}

# Extracting a committed file is not authoring one. Measured false positive (2026-10-22):
#
#   git show main:.cupcake/tests/teardown_must_relaunch_test.rego > <scratchpad>/old_test.rego
#
# was refused as a script authored into /tmp, but every byte came out of git, where the file is
# already versioned and reviewable -- the two things this guard exists to protect. So one shape
# is admitted, and only as the whole command: `git [-C <dir>] show <rev>:<path>` or
# `git [-C <dir>] cat-file -p <rev>:<path>`, its stdout sent with a single `>` to one path under
# /tmp. Each token excludes whitespace, `;&|<>`, `$`, backticks, parentheses, quotes and
# backslashes, so nothing can be chained, piped, substituted or appended to the file in the same
# command. The rev must be non-empty and must not start with `-`: an empty rev (`:<path>`) reads
# the index, which any `git add` can fill with hand-written bytes, and a leading dash would be an
# option such as `--output=` rather than a revision.
#
# One `cd <dir> && ` may lead it, with the target then relative to that directory (2026-11-10),
# so `cd <scratchpad> && git show main:x.rego > old.rego` gets the verdict of its absolute
# spelling. A `cd` writes nothing, and its directory token has the same exclusions.
versioned_blob_extraction_pattern := `^(?:cd[ \t]+[^\s;&|<>$\x60()'"\\]+[ \t]*&&[ \t]*)?git(?:[ \t]+-C[ \t]+[^\s;&|<>$\x60()'"\\]+)?[ \t]+(?:show|cat-file[ \t]+-p)[ \t]+[^\s;&|<>$\x60()'"\\:\-][^\s;&|<>$\x60()'"\\:]*:[^\s;&|<>$\x60()'"\\]+[ \t]*>[ \t]*[^\s;&|<>$\x60()'"\\]+$`

versioned_blob_extraction if regex.match(versioned_blob_extraction_pattern, trim_space(command))
