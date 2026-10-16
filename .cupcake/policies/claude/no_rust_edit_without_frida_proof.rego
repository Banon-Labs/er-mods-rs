# METADATA
# scope: package
# title: No Rust edit under crates/ without a Frida measurement behind it
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-NO-RUST-EDIT-WITHOUT-FRIDA-PROOF
#   description: >-
#     Refuse a Write/Edit to `crates/**/*.rs` unless a Frida session has attached to
#     the game and reported observations since the last commit.
#
#     User directive 2026-09-16. AGENTS.md has said it for months -- "The order is
#     Frida, then Frida, then Frida, and only then a DLL: prototype with it, run the
#     experiment with it, and fix the thing with it if a hook can. Build a DLL when
#     the mechanism is already known and the code is the product, never to find
#     something out" -- and on this day the agent did the opposite twice in one
#     session: it tore the user's game down, ran a build, relaunched, and did it again,
#     to land a change whose mechanism it had never measured. The justification it gave
#     itself was an argument about a prior A/B it had already shown was confounded.
#     The user's answer was that a note is not a rule, so here is the rule.
#
#     The tell this encodes: when the reason for an edit is reasoning rather than a
#     measurement, the edit is a guess, and a guess here costs a teardown, a build and
#     a relaunch of somebody's game -- against an agent file that reloads in place for
#     nothing.
#
#     What opens the gate is `scripts/er-frida-evidence.py --check` printing PROVEN,
#     which needs a session that attached to a pid and received at least one message,
#     recorded after the newest Rust commit to the crate being edited. The signal pipes
#     the pending event to the reader, which maps each target to its crate under
#     `crates/` or `third_party/`; a target outside any crate, or no target, falls back
#     to every `crates/**/*.rs` commit. The log lives under XDG_STATE_HOME, not in
#     the repo, so the Write tool being gated cannot forge its own permission. A commit
#     to that crate spends the evidence: one measurement licenses one change.
#
#     Deliberately NOT carved out: tests, doc comments, host-only crates. Every carve-out
#     is a door, and the failure this exists to stop was the agent walking through the
#     door it argued for itself. `scripts/`, `.cupcake/`, docs and every non-Rust file
#     stay editable, which is what makes it possible to build and fix this gate.
#
#     A SECOND INSTRUMENT was added 2026-09-19, and it is not one of those doors. It
#     exempts no file: it accepts a different measurement, taken at the branch by our own
#     DLL and quoted verbatim out of a live run's log, for the class Frida physically
#     cannot see -- our own unexported internals in a `cdylib` that exports only `DllMain`.
#     It is strictly narrower than the Frida path, because it opens the one crate whose
#     log it came from rather than the whole tree. The three conditions that keep it a
#     measurement (verbatim line, log newer than the last committed Rust change, real
#     crate) are enforced when the record is written, by the evidence reader.
#     A THIRD INSTRUMENT is not what arrived 2026-09-22 -- the Bash tool did. The rule
#     routed on the write TOOLS and denied on `tool_input.file_path`, and a Bash call
#     carries no such key, so the rule was undefined for it and the gate never saw the
#     edit. Measured (bd er-effects-rs-wuij): a `for c in ...; do sed -i ... crates/$c/src/config.rs;
#     done` rewrote four crate sources with no measurement behind it, and the very next
#     Edit-tool call adding a `pub fn` to the four `lib.rs` files beside them was refused.
#     Same change, same files, two different answers, decided by which tool typed it. The
#     description above says every carve-out is a door and the failure it exists to stop
#     was the agent walking through the door it argued for itself; this one nobody argued
#     for.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"]
#     required_signals: ["frida_evidence"]
package cupcake.policies.claude.no_rust_edit_without_frida_proof

import rego.v1

import data.cupcake.system.commands

# Defaulted at both levels for the same reason the signal below is: `input.tool_input` read
# directly is undefined when the key is absent, and an undefined term anywhere in the deny body
# takes the whole rule with it. Nothing in this file may depend on a key being present.
file_path := object.get(object.get(input, "tool_input", {}), "file_path", "")

# Rust source that compiles into something the game loads. The gate is the file
# extension plus the directory, so a `.toml`, a `.md` or a script is never caught.
rust_under_crates if {
	endswith(file_path, ".rs")
	contains(file_path, "/crates/")
}

rust_under_crates if {
	endswith(file_path, ".rs")
	startswith(file_path, "crates/")
}

# The verdict line from `scripts/er-frida-evidence.py --check`.
#
# Read through a defaulted `signals` object rather than `input.signals` directly. The
# engine attaches signals for a policy that declares `required_signals`, but the direct
# form is undefined when the key is absent, and an undefined term in the deny body below
# makes the whole rule undefined -- the gate would open on exactly the input that proves
# least. Measured by `test_deny_when_the_signals_object_is_missing_entirely`, which failed
# against the direct form.
signals := object.get(input, "signals", {})

raw_evidence := object.get(signals, "frida_evidence", "")

# A signal whose command exits non-zero does not reach a policy as its output at all: cupcake
# replaces the string with a failure record, an object carrying `error`, `exit_code`, `output`
# and `success`. Every string operation on that object is then undefined, and an undefined term
# in the deny body takes the whole rule with it -- so the gate opens on exactly the input that
# proves least.
#
# That is not hypothetical. Measured 2026-09-16 against cupcake 0.5.2: this signal script was the
# only one in `.cupcake/signals/` without its executable bit, an auto-discovered signal is exec'd
# directly rather than through `bash`, and the result was `Signal 'frida_evidence' failed with
# exit code 126` on every evaluation. `cupcake eval` allowed a `crates/` edit with no measurement
# behind it while `opa test` was green, because the tests all handed the rule a string.
#
# Coercing a non-string to the empty string makes that case deny instead, and the reason below
# says which of the two nothings it was.
evidence := raw_evidence if {
	is_string(raw_evidence)
}

evidence := "" if {
	not is_string(raw_evidence)
}

# Anything that is not a `PROVEN` line leaves this undefined and the deny below fires --
# including an empty signal, which is what a broken or timed-out reader produces. Failing
# closed is the point: a gate that opens when its evidence reader breaks is not a gate.
#
# A Frida verdict opens every crate, because the instrument reaches the game and the game is what
# all of this eventually talks to.
proven if {
	proven_for(file_path)
}

proven_for(_) if {
	startswith(evidence, "PROVEN")
	not telemetry_verdict
}

# A telemetry verdict opens exactly one crate: the shell whose own log the quoted line came out of.
#
# The blind spot this closes, measured 2026-09-19. Frida reaches the GAME; it does not reach our
# own DLLs. A release `cdylib` here exports `DllMain` and nothing else, so an unexported Rust
# static, a `pub(crate)` seam, or "which of our functions calls which of our setters" has no
# address to attach to and no name to resolve. `er-save-game-row` served its destination browser
# undressed for exactly that reason -- `gfx_swap::set_profile_05_010_edit_armed` has one caller and
# that shell does not go through it -- and the gate answered a provable defect by demanding an
# instrument which cannot see it. An agent facing that either stalls or reaches for one of the
# forgery routes `scripts/er-frida-evidence.py` names in its own docstring, and neither is the
# behaviour this was written to get.
#
# What makes it evidence rather than an argument is enforced at record time, not here: the quoted
# line must be present verbatim in the named log, the log must be newer than the last committed
# Rust change, and the crate must exist. What is enforced HERE is the scope -- a measurement of one
# shell's branch says nothing about any other crate, so it may not open one. That makes this path
# narrower than the Frida path above, which opens the whole tree.
proven_for(path) if {
	telemetry_verdict
	contains(path, concat("", ["crates/", licensed_crate, "/"]))
}

# Keyed on the two fixed words alone, so a verdict that has lost its `crate=` field is still
# recognised as telemetry and is refused by the rule above rather than falling through to the
# unscoped one.
telemetry_verdict if {
	startswith(evidence, "PROVEN telemetry")
}

# Anchored at the front of the verdict: everything to the right of the crate name is free text
# from a log, and free text must not be able to impersonate the field that decides scope.
licensed_crate := name if {
	matches := regex.find_all_string_submatch_n(`^PROVEN telemetry crate=([A-Za-z0-9_-]+)(?: |$)`, evidence, 1)
	count(matches) == 1
	name := matches[0][1]
}

block_reason := "🧁 Cupcake blocked a Rust edit with no Frida measurement behind it. AGENTS.md: \"The order is Frida, then Frida, then Frida, and only then a DLL: prototype with it, run the experiment with it, and fix the thing with it if a hook can. Build a DLL when the mechanism is already known and the code is the product, never to find something out.\"\n\nGo and look first:\n  python3 scripts/er-frida-up.py\n  uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/<agent>.js\n\nThe watch records what it saw on exit, and `python3 scripts/er-frida-evidence.py --check` is what opens this gate. It needs a session that attached to a pid and received at least one message -- a watch that observed nothing did not look at anything. A commit spends the evidence, so the next change needs its own measurement.\n\nIf the mechanism is inside one of OUR DLLs rather than in the game -- an unexported static, a `pub(crate)` seam, which of our functions calls which of our setters -- Frida has nothing to attach to, and the instrument that reaches it is the shell's own in-process telemetry from a live run:\n  python3 scripts/er-frida-evidence.py --record-telemetry --crate <crate> --log <the run's log> --line '<a line from it, verbatim>'\nThe line must be in that log word for word and the log must be newer than the last committed Rust change. It opens that ONE crate.\n\nIf neither instrument can reach it, say so in one sentence and say what can. Do not edit around this by narrowing the change until it looks harmless.\n\nEditable without proof: `scripts/`, `.cupcake/`, docs, and every non-Rust file."

# What the refusal quotes back. The three cases are worth telling apart: a verdict line is the
# reader answering, an absent signal is nobody having asked, and a failure record is the reader
# being broken -- which is a bug to fix rather than a measurement to go and take.
said := evidence if {
	is_string(raw_evidence)
	raw_evidence != ""
}

said := "<signal absent>" if {
	is_string(raw_evidence)
	raw_evidence == ""
}

said := "<the frida_evidence signal failed; cupcake replaced its output with a failure record, so it exited non-zero. Check that .cupcake/signals/frida_evidence.sh is executable and runs>" if {
	not is_string(raw_evidence)
}

# The tools that only look. Everything else carrying a `crates/**/*.rs` path is treated as a
# write, so a write tool nobody thought to list is still refused.
#
# Routing metadata was the whole guard until 2026-09-16, when a subagent reading
# `crates/er-telemetry-core/src/counters.rs` with the Read tool was refused by this policy and
# could not find anything in `.cupcake/` that explained it. A gate whose purpose is to make an
# agent go and look must never be the thing that stops it looking, and a reader has nothing to
# offer as evidence because it changes nothing.
#
# The previous shape was argued for rather than measured: a test here recorded that a hand-built
# Read event does reach the deny, and reasoned that it did not matter because routing keeps Read
# out. It did matter. Routing is an optimisation; the deny body is the contract. Listing readers
# rather than writers keeps the fail-closed direction the old argument was right about.
read_only_tools := {"Read", "Grep", "Glob", "NotebookRead", "WebFetch", "WebSearch", "LSP"}

tool_name := object.get(input, "tool_name", "")

# ---------------------------------------------------------------------------
# THE SAME GATE FROM THE BASH TOOL (2026-09-22, bd er-effects-rs-wuij)
#
# Everything above reads `tool_input.file_path`, which a Bash call does not have. What
# follows asks the same question of a command's text: does this command WRITE a
# `crates/**/*.rs` path? It is deliberately written to catch the write verbs and to leave
# every read alone, because a gate whose purpose is to make an agent go and look must never
# be the thing that stops it looking -- the same reasoning that put `Read` in the list above.
#
# A DESTINATION THE SHELL COMPUTES (2026-10-15). Asking for a literal `crates/` in the
# written word left every indirect spelling open, and one was walked through: a subagent
# wrote two crate sources with `sed ... > $W/lib.rs`, where `W` held
# `.worktrees/er-render-scale/crates/er-overlay-theme/src`. So a written word is now read
# for where it lands, not only for what it says:
#   * `$NAME` at its front is replaced by every literal value the same command assigns to
#     `NAME`, and `$HOME` by a home directory, which is never under `crates/`;
#   * a relative word is also read against `input.cwd`, against every `cd`/`pushd`
#     operand and against every `git -C` directory in the command;
#   * `cp`, `mv`, `ln`, `rsync` and `install` into a directory write the source's name
#     there;
#   * `xargs <writer>` and `find ... -exec <writer>` / `-delete` write paths the command
#     never spells at all.
# When the guard cannot reduce a written word to literal paths -- a substitution, a
# variable it cannot see assigned, a `cd` into one -- it refuses whenever the command
# names `crates` or `.rs` anywhere, unless the word ends in a literal extension that is
# not `.rs`: `> $S/build.log` cannot be a crate source whatever `S` holds. That is the
# fail-closed direction this file takes everywhere else; the way through is to spell the
# path, or to assign it from a literal in the same command.
#
# RESIDUE, stated rather than hidden. These write paths are invisible here and none is
# closed by pretending otherwise:
#   * `git apply <patch>` and `patch < <diff>`, where the target path lives in the patch
#     file and not in the command;
#   * `cargo fmt` / `cargo clippy --fix`, which rewrite sources while naming none;
#   * a committed `python3 scripts/<name>.py` that writes a crate source, which the
#     sibling `bash_no_python_file_write` exempts by design;
#   * an indirect write in a command that names neither `crates` nor `.rs` --
#     `for f in $(git diff --name-only); do sed -i ... $f; done` -- because refusing every
#     computed destination would refuse every `> $LOG` in the repo;
#   * a writer started from inside `$(...)`, whose program sits behind an assignment word;
#   * a heredoc body in the delivered command, which arrives welded to the line that opens
#     it, so a body line such as `> $W/lib.rs` reads as a write and is refused. That is
#     the over-refusal direction; write such a body with the Write tool instead.
# The first two are mechanical transformations rather than the guessed change this gate
# exists to stop. The third is a reviewed script, which is the same argument.

# Words of one segment. Tabs folded first, because a segment arrives as raw shell text and
# `split` on a single space would otherwise weld a tab-separated pair into one word.
segment_words(segment) := [word |
	some word in split(replace(segment, "\t", " "), " ")
	word != ""
]

# The program this segment runs: the first word that is not a wrapper. A segment carries no
# separators -- `shell_segments` has already split on them -- so "first non-wrapper" and "in
# a command slot" are the same word, and this takes the cheaper of the two readings.
#
# Cheaper is load-bearing, not a preference. The first draft asked
# `word_in_command_slot(words, index)` for every index, and that predicate counts the words
# before `index`, so the rule was quadratic in the length of a segment. A 3.5 KB command
# whose heredoc body is one segment of ~400 words exhausted the engine's wasm memory and
# aborted the whole evaluation -- which is not a refusal but `{}` at exit 0, every policy
# silent at once. Measured 2026-09-22, while adding the tests below.
#
# Only the first 64 words are asked (2026-10-15). Wrappers come first, so the program stands
# among them unless 64 words of wrappers precede it, and the bound keeps the cost of the call
# fixed on a heredoc body of a thousand words.
program_index(words) := index if {
	indexes := [j |
		some j, _ in array.slice(words, 0, 64)
		not stands_before_program(words, j)
	]
	count(indexes) > 0
	index := min(indexes)
}

# The shared wrappers, plus what stands in front of a program in this policy's reading only:
# the compound-command openers, and `xargs` with the arguments of its options. `xargs sed -i`
# runs `sed -i` on paths it reads from its input, which `unresolved_writes` below exists for.
stands_before_program(words, j) if commands.command_slot_wrapper_at(words, j)

stands_before_program(words, j) if words[j] in {"{", "!", "if", "elif", "while", "until", "time"}

stands_before_program(words, j) if program_named(words[j], "xargs")

stands_before_program(words, j) if {
	j > 0
	words[j - 1] in {"-I", "-d", "-a", "-E", "-s"}
}

program_named(word, name) if word == name

program_named(word, name) if endswith(word, concat("", ["/", name]))

# ---------------------------------------------------------------------------
# One segment, read once.
#
# Every rule below asks the same questions of a segment -- which word is the program, does it
# write every word it is given, where is its destination -- and the first draft of this change
# asked them again for every word it looked at, which is quadratic in the length of a segment:
# `program_index` is a pass over the segment, and `segment_writes` ran it once per clause per
# word. The answers are now taken once per segment, and the work per word is constant.
#
# The ceiling this has to live under, measured in place on 2026-10-15 against cupcake 0.5.2 with
# the regression runner's signal overrides: the wasm module aborts with `opa_malloc: failed` on a
# `tee` heredoc of about 60 lines of shell, at HEAD `1a4a8b09` and with this change alike, and at
# every memory limit. That is the regex-cache defect bd `cupcake-wasm-regex-cache-reuse-oob-2026-09-16`
# records, not this file running out of room, and it moves with the layout of the whole rulebook.
# Measure a change here with `cupcake eval` from a real checkout: from any other directory
# cupcake loads no project signals, and the ceiling it reports there is not this one.

# Every segment of every executed text, with the spaces inside its quoted spans protected where
# the text's quoting is known.
text_segments contains segment if {
	some text in commands.input_executed_texts
	clean := cleanly_quoted(text)
	some raw in commands.shell_segments(text)
	segment := quoted_spaces_protected(clean, raw)
}

segment_facts[segment] := {"words": words, "info": segment_info(words)} if {
	some segment in text_segments
	words := segment_words(segment)
}

# In a text the shared decomposition read cleanly, every quote left is a real delimiter and no
# quoted span crosses a segment. Protecting the spaces inside each span makes the span one word,
# so a quoted destination with a space in it is read whole, and prose stops being words at all:
# `git commit -m "sed x > $W/lib.rs"` has no `>` word to read as a redirect. Elsewhere the quotes
# cannot be trusted, the segment is split as it stands, and every word counts, which is the
# direction that refuses more.
quoted_spaces_protected(true, segment) := spans_protected(spans_protected(segment, `"`), "'")

quoted_spaces_protected(false, segment) := segment

# Whether `commands.scan_text` resolved this text's quoting. Blanking its spans again changes
# nothing, which is the test: a text kept raw because its quoting could not be read is not a fixed
# point. A substitution or an escaped quote is never in a resolved text, and is excluded first
# because a raw text carrying one can still balance by accident.
#
# `blank_spans` and not the whole of `operands_blanked`, on purpose: the latter runs the heredoc
# regexes over the whole command a second time, and the wasm runtime's regex cache is the defect
# described above. Nothing in this file calls a `commands` helper that reaches a regex over the
# whole command.
cleanly_quoted(text) := true if {
	not contains(text, "$(")
	not contains(text, "`")
	not contains(text, `\"`)
	not contains(text, `\'`)
	commands.blank_spans(text, `"`, "'") == text
} else := false

spans_protected(segment, quote) := concat(quote, [part |
	some i, raw in parts
	part := span_protected(i, raw)
]) if {
	parts := split(segment, quote)
	count(parts) % 2 == 1
} else := segment

span_protected(i, raw) := raw if i % 2 == 0

span_protected(i, raw) := replace(replace(raw, " ", protected_space), "\t", protected_space) if i % 2 == 1

protected_space := "\u0001"

segment_info(words) := info if {
	start := program_start(words)
	editor := editor_position(words, start)
	flagged := has_script_flag(words, editor)
	base := program_base(words, start)
	info := {
		"start": start,
		"base": base,
		"every_word": writes_every_word(words, start, base, editor),
		"destination": copy_destination(words, start, base),
		"editor": editor,
		"script": positional_script(words, editor, flagged),
	}
}

# A segment with no program -- 64 wrappers, or nothing but options -- still has its redirects
# read; it just has no program for the rules that need one.
program_start(words) := program_index(words) if {
	program_index(words)
} else := count(words)

# A subshell's `(` is welded to the first word inside it, so `(cd crates/x && ...)` would
# otherwise run a program called `(cd`.
program_base(words, start) := basename(trim_left(words[start], "(")) if {
	start < count(words)
} else := ""

writes_every_word(words, start, base, editor) := true if {
	every_word_writer(words, start, base, editor)
} else := false

# Programs whose whole job is to write the file they are handed.
every_word_writer(_, _, base, _) if base in {"tee", "patch", "truncate", "install", "dd", "shred"}

# In-place editors, which read like a filter until the flag is there.
every_word_writer(words, _, _, editor) if {
	editor >= 0
	some word in words
	in_place_flag(word)
}

every_word_writer(words, start, "git", _) if git_writes(words, start)

# `git` by subcommand: `checkout`, `restore`, `apply`, `rm`, `mv`, `clean` and `stash`
# rewrite the working tree; `diff`, `log`, `show`, `grep` and `blame` read it.
git_writes(words, start) if git_subcommand(words, start) in {"apply", "checkout", "clean", "stash", "rm", "mv"}

# `git restore` writes the working tree by default, and only the index under `--staged`/`-S`.
# Refused 2026-10-02: `git restore --staged <paths>` was denied as if it rewrote the sources,
# when all it does is unstage them. `--worktree`/`-W` beside `--staged` writes both, so it
# still counts. `git reset [HEAD] -- <paths>` is not a writer at all and is not listed: with
# paths it only resets index entries.
git_writes(words, start) if {
	git_subcommand(words, start) == "restore"
	restore_writes_worktree(words)
}

# The subcommand, for the programs whose verb is their second word: `git checkout` writes
# and `git diff` does not, and the difference is not visible in the program alone.
#
# `git -C <dir> checkout -- <path>` puts a directory where the subcommand would be, so the
# arguments of git's own leading options are skipped. Before 2026-10-15 that spelling had
# `<dir>` as its subcommand and wrote crate sources unseen.
git_subcommand(words, start) := words[index] if {
	indexes := [j |
		some j, word in words
		j > start
		not startswith(word, "-")
		not words[j - 1] in git_options_with_an_argument
	]
	count(indexes) > 0
	index := min(indexes)
}

git_options_with_an_argument := {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}

restore_writes_worktree(words) if {
	not restore_has_flag(words, "--staged", "S")
}

restore_writes_worktree(words) if {
	restore_has_flag(words, "--worktree", "W")
}

# The long spelling, or the short letter alone or inside a cluster such as `-SW`. Only words
# before a `--` count, because everything after it is a pathspec.
restore_has_flag(words, long, _) if {
	some index, word in words
	word == long
	before_end_of_options(words, index)
}

restore_has_flag(words, _, letter) if {
	some index, word in words
	startswith(word, "-")
	not startswith(word, "--")
	contains(word, letter)
	before_end_of_options(words, index)
}

before_end_of_options(words, index) if {
	not "--" in array.slice(words, 0, index)
}

# Where `cp`, `mv`, `ln`, `rsync` and `install` put the file they write: the directory after
# `-t`, or else the last operand. The last operand is what tells `cp crates/a.rs /tmp/b.rs` (a
# read of the crate) from `cp /tmp/b.rs crates/a.rs` (a write of it). -1 for every other program.
copy_destination(words, start, base) := index if {
	base in {"cp", "mv", "ln", "rsync", "install"}
	index := copy_destination_index(words, start)
} else := -1

copy_destination_index(words, start) := j + 1 if {
	start >= 0
	some j, word in words
	word in {"-t", "--target-directory"}
} else := last_operand_index(words, start)

# A redirect and its target are not operands, so `cp a b 2>/dev/null` still writes `b`.
last_operand_index(words, start) := index if {
	indexes := [j |
		some j, _ in words
		j > start
		operand_word(words, j)
	]
	count(indexes) > 0
	index := max(indexes)
}

operand_word(words, j) if {
	not startswith(words[j], "-")
	not redirect_word(words[j])
	not startswith(words[j], "<")
	not follows_redirect(words, j)
}

follows_redirect(words, j) if {
	j > 0
	redirect_token(words[j - 1])
}

follows_redirect(words, j) if {
	j > 0
	words[j - 1] in {"<", "<<<"}
}

# Where the editor stands. `perl` and `ruby` are interpreters to the shared command-slot
# reading, which steps over them to find a script, so the program is never one of them: the
# perl and ruby halves of this rule could not fire until 2026-10-15. The editor is looked for
# among the words up to the program instead. -1 when there is none.
editor_position(words, start) := min(indexes) if {
	indexes := [j |
		some j, word in array.slice(words, 0, start + 1)
		basename(trim_left(word, "(")) in {"sed", "perl", "ruby"}
	]
	count(indexes) > 0
} else := -1

# `-i`, `-i.bak`, and a cluster carrying `i` -- `perl -pi`, `sed -ni`, `sed -Ei`. Until
# 2026-10-15 only a word starting `-i` counted, so `perl -pi -e ... crates/x/src/lib.rs`, the
# commonest perl spelling of an in-place edit, was not a write. The cluster is read up to its
# first non-letter and must be made of flag letters these three programs take, so a perl
# `-MList::Util=min` is not mistaken for one.
in_place_flag(word) if startswith(word, "-i")

in_place_flag(word) if startswith(word, "--in-place")

in_place_flag(word) if {
	startswith(word, "-")
	not startswith(word, "--")
	cluster := split(substring(word, 1, count(word)), ".")[0]
	contains(cluster, "i")
	count([c |
		some c in split(cluster, "")
		not contains("abEeilnprsuwz0", c)
	]) == 0
}

# The script an in-place editor runs is not a file it writes, and its `$` is the regex
# anchor or a value, not a destination: `sed -i "s/$A/$B/" scripts/x.py` writes one file.
# The script is the word after `-e`/`-f` or a cluster ending in `e` (`perl -pe`), or, when
# there is none of those, the first operand after the editor.
has_script_flag(words, editor) := true if {
	editor >= 0
	some word in words
	script_flag(word)
} else := false

positional_script(words, editor, flagged) := min(operands) if {
	editor >= 0
	flagged == false
	operands := [j |
		some j, word in words
		j > editor
		not startswith(word, "-")
	]
	count(operands) > 0
} else := -1

script_flag(word) if word in {"-e", "-f", "--expression", "--file"}

script_flag(word) if {
	startswith(word, "-")
	not startswith(word, "--")
	endswith(word, "e")
}

script_word(info, words, index) if {
	info.editor >= 0
	index > 0
	script_flag(words[index - 1])
}

script_word(info, _, index) if info.script == index

# Whether the word at `index` is one this segment writes.
writes_at(_, words, index) if redirect_word(words[index])

writes_at(_, words, index) if {
	index > 0
	redirect_token(words[index - 1])
}

writes_at(info, _, _) if info.every_word

writes_at(info, _, index) if info.destination == index

# A move also removes every source it is given, which is a write of each of them.
writes_at(info, words, index) if {
	info.base == "mv"
	index > info.start
	operand_word(words, index)
}

# Removal destroys every operand it is given.
writes_at(info, _, index) if {
	info.base == "rm"
	index > info.start
}

# A program that writes the paths it is given, whatever they are.
writes_given_paths(info) if info.every_word

writes_given_paths(info) if info.base in {"cp", "mv", "ln", "rsync", "rm"}

redirect_word(word) if startswith(word, ">")

redirect_word(word) if fd_redirect_word(word)

# `2>file`, `1>>file`: a file descriptor number welded to the redirect.
fd_redirect_word(word) if {
	cut := indexof(word, ">")
	cut > 0
	count([c |
		some c in split(substring(word, 0, cut), "")
		not commands.digit_char(c)
	]) == 0
}

redirect_token(word) if {
	startswith(word, ">")
}

redirect_token(word) if {
	endswith(word, ">")
}

redirect_token(word) if {
	endswith(word, ">>")
}

# ---------------------------------------------------------------------------
# Where a written word lands.

# A Rust source under `crates/`.
crate_rust_path(path) if {
	endswith(path, ".rs")
	contains(path, "crates/")
}

# What a written word names once the shell has taken its syntax off: the redirect in front
# of it (`>file`, `2>>file`, `>|file`), its quotes, and the `)` that closes a subshell it
# ends. Quotes matter because `sed -i x "crates/a/src/lib.rs"` ends in a quote and so did not
# end in `.rs` until 2026-10-15.
target_text(word) := replace(trim_right(quotes_dropped(after_redirect(word)), ")"), protected_space, " ")

after_redirect(word) := trim_left(pieces[count(pieces) - 1], "|") if {
	redirect_word(word)
	pieces := split(word, ">")
} else := word

quotes_dropped(text) := replace(replace(text, "'", ""), `"`, "")

# A `$` or a backtick outside single quotes, which is where the shell computes text.
expands(word) if {
	some i, part in split(word, "'")
	i % 2 == 0
	contains(part, "$")
}

expands(word) if {
	some i, part in split(word, "'")
	i % 2 == 0
	contains(part, "`")
}

# What resolving a word needs from the rest of the command, gathered once and handed to the
# functions below as an argument rather than read by them, so no rule is read from inside a loop
# over words. The written words are deduplicated before any of them is resolved, so a word that
# a heredoc repeats a thousand times is resolved once.
resolution_base := {"assigned": assigned_values, "bare": command_words}

resolution := {
	"assigned": assigned_values,
	"bare": command_words,
	"directories": literal_directories,
	"unresolved_directory": directory_unresolved,
	"cwd": object.get(input, "cwd", ""),
}

directory_unresolved := true if {
	unresolved_directory
} else := false

# The literal paths a written word can name. Undefined when the guard cannot reduce it to
# literals, which is what an `unresolved` finding records.
literal_forms(word, _) := {target_text(word)} if {
	not expands(word)
}

literal_forms(word, context) := resolved_paths(target_text(word), context) if {
	expands(word)
}

# `$NAME` or `${NAME}` at the front of a path, replaced by every literal value the command
# assigns to `NAME`. All of them, because a later assignment or a branch can pick any one. A
# name that also stands as a bare word -- `for NAME in ...`, `read NAME` -- takes values the
# command does not spell, and so does a name with no assignment at all.
resolved_paths(path, context) := paths if {
	[name, rest] := leading_variable(path)
	not expands(rest)
	not name in context.bare
	values := [value | some pair in context.assigned; pair[0] == name; value := pair[1]]
	count(values) > 0
	literals := [literal | some value in values; literal := literal_value(value)]
	count(literals) == count(values)
	paths := {concat("", [literal, rest]) | some literal in literals}
}

# `$HOME`, unassigned. Whatever it holds, it is not a crate directory, and `~` keeps the path
# absolute for the joins below.
resolved_paths(path, context) := {concat("", ["~", rest])} if {
	[name, rest] := leading_variable(path)
	name == "HOME"
	not expands(rest)
	count([pair | some pair in context.assigned; pair[0] == "HOME"]) == 0
}

leading_variable(path) := [name, rest] if {
	startswith(path, "${")
	close := indexof(path, "}")
	close > 2
	name := substring(path, 2, close - 2)
	shell_name(name)
	rest := substring(path, close + 1, count(path))
}

leading_variable(path) := [name, rest] if {
	startswith(path, "$")
	not startswith(path, "${")
	tail := substring(path, 1, count(path))
	stops := [i | some i, c in split(tail, ""); not contains(commands.shell_name_chars, c)]
	end := min(array.concat(stops, [count(tail)]))
	end > 0
	name := substring(tail, 0, end)
	rest := substring(tail, end, count(tail))
}

shell_name(name) if {
	name != ""
	count([c | some c in split(name, ""); not contains(commands.shell_name_chars, c)]) == 0
}

assigned_values contains [name, value] if {
	some word in command_words
	commands.shell_assignment_word(word)
	cut := indexof(word, "=")
	name := substring(word, 0, cut)
	value := substring(word, cut + 1, count(word))
}

command_words contains word if {
	some _, facts in segment_facts
	some word in facts.words
}

# A value the shell takes as written: no expansion, and quotes only as one pair around it. A
# quoted value that spaces split (`W="/a b"` in a text whose quoting could not be read)
# arrives as `"/a` and is refused here.
literal_value(value) := value if {
	not expands(value)
	not contains(value, `"`)
	not contains(value, "'")
}

literal_value(value) := replace(inner, protected_space, " ") if {
	count(value) >= 2
	some quote in [`"`, "'"]
	startswith(value, quote)
	endswith(value, quote)
	inner := substring(value, 1, count(value) - 2)
	not contains(inner, quote)
	literal_inner(quote, inner)
}

literal_inner("'", _)

literal_inner(`"`, inner) if not expands(inner)

# Where a literal path can land: itself, and when relative, read from the session's working
# directory and from every directory the command moves into first.
placed(path, context) := {path} if {
	absolute(path)
} else := forms if {
	from_directories := {joined |
		some directory in context.directories
		some joined in directory_joins(directory, path, context.cwd)
	}
	forms := ({path} | cwd_joins(path, context.cwd)) | from_directories
}

absolute(path) if startswith(path, "/")

absolute(path) if startswith(path, "~")

cwd_joins(path, cwd) := {concat("/", [cwd, path])} if {
	cwd != ""
} else := set()

directory_joins(directory, path, cwd) := {joined} if {
	absolute(directory)
	joined := concat("/", [directory, path])
} else := {joined} | cwd_joins(joined, cwd) if {
	joined := concat("/", [directory, path])
}

# Every directory a relative path in this command may be read against: `cd` and `pushd`
# operands, and `git -C` directories.
directory_operands contains operand if {
	some _, facts in segment_facts
	facts.info.base in {"cd", "pushd"}
	operand := directory_operand(facts.words, facts.info.start)
}

directory_operands contains operand if {
	some _, facts in segment_facts
	facts.info.base == "git"
	some j, word in facts.words
	word == "-C"
	operand := facts.words[j + 1]
}

directory_operand(words, start) := words[min(operands)] if {
	operands := [j | some j, word in words; j > start; directory_word(word)]
	count(operands) > 0
} else := "~"

directory_word(word) if not startswith(word, "-")

directory_word("-")

literal_directories contains directory if {
	context := resolution_base
	some operand in directory_operands
	operand != "-"
	some directory in literal_forms(operand, context)
}

# `cd -`, or a directory the guard cannot reduce to a literal.
unresolved_directory if "-" in directory_operands

unresolved_directory if {
	context := resolution_base
	some operand in directory_operands
	operand != "-"
	not literal_forms(operand, context)
}

# A literal extension that is not `.rs`: `$S/build.log`, `${F}.bak`, `"$OUT/x.json"`. Whatever
# the variable holds, the file is not Rust.
provably_not_rust(path) if {
	parts := split(path, ".")
	count(parts) > 1
	extension := parts[count(parts) - 1]
	extension != ""
	extension != "rs"
	count([c | some c in split(extension, ""); c in {"$", "`", "/", "}", ")", "*", "?", "["}]) == 0
}

# Words that can name a destination worth resolving. Checked before anything that costs more.
worth_reading(word) if {
	some fragment in [".rs", "$", "`", `"`, "'"]
	contains(word, fragment)
}

# ---------------------------------------------------------------------------
# What the command writes, as `[kind, target]` pairs: `crate` for a `crates/**/*.rs` path,
# `unresolved` for a destination the guard cannot reduce to literal paths.

# Every distinct word the command writes. A set, so a word repeated a thousand times in a
# heredoc is resolved once.
written_words contains word if {
	some _, facts in segment_facts
	words := facts.words
	info := facts.info
	some index, word in words
	worth_reading(word)
	writes_at(info, words, index)
	not script_word(info, words, index)
}

# Measured in place against HEAD `1a4a8b09` on 2026-10-15, the longest command of four heredoc
# and payload shapes that still evaluates moved from 1,057 / 60 / 1,750 / 360 lines to
# 1,016 / 60 / 1,563 / 360 with this change and the two beside it. Before reshaping these rules,
# repeat that measurement -- `opa test` cannot see the wasm runtime at all.
bash_findings contains finding if {
	context := resolution
	some word in written_words
	some finding in word_findings(word, context)
}

# A word the guard can reduce to literal paths yields each crate path among them, and, after a
# `cd` it cannot follow, each relative one that could be Rust. A word it cannot reduce yields
# itself, unless its extension rules Rust out.
word_findings(word, context) := found if {
	forms := literal_forms(word, context)
	crate_paths := {["crate", path] |
		some form in forms
		some path in placed(form, context)
		crate_rust_path(path)
	}
	relative := {["unresolved", form] |
		context.unresolved_directory
		some form in forms
		not absolute(form)
		not provably_not_rust(form)
	}
	found := crate_paths | relative
} else := found if {
	target := target_text(word)
	found := {["unresolved", target] | not provably_not_rust(target)}
}

# `cp`, `mv`, `ln`, `rsync` and `install` into a directory write the source's own name there:
# `cp /tmp/lib.rs crates/x/src/` is a write of `crates/x/src/lib.rs`, and until 2026-10-15 it
# read as a write of a directory. A destination that ends in `/` or carries no extension is
# taken for a directory.
#
# `[destination, joined]`: each Rust source a copy names, joined to a destination that reads as
# a directory. Distinct pairs, for the reason `written_words` is a set. The destination is
# resolved against the command's assignments; `bash_findings` places the joined path.
bash_findings contains ["crate", path] if {
	some pair in copies_into_directories
	some path in placed(pair[1], resolution)
	crate_rust_path(path)
}

copies_into_directories contains [destination, joined] if {
	context := resolution_base
	some _, facts in segment_facts
	info := facts.info
	info.destination >= 0
	words := facts.words
	destination := words[info.destination]
	some source_index, source_word in words
	source_index > info.start
	source_index != info.destination
	operand_word(words, source_index)
	source := target_text(source_word)
	endswith(source, ".rs")
	some form in literal_forms(destination, context)
	directory_like(form)
	joined := concat("/", [trim_right(form, "/"), basename(source)])
}

directory_like(path) if endswith(path, "/")

directory_like(path) if not contains(basename(path), ".")

directory_like(path) if basename(path) in {".", ".."}

basename(path) := parts[count(parts) - 1] if {
	parts := split(path, "/")
}

# Writes whose paths the command never spells.
bash_findings contains ["unresolved", "<the paths xargs reads from its input>"] if {
	some _, facts in segment_facts
	some word in array.slice(facts.words, 0, facts.info.start)
	program_named(word, "xargs")
	writes_given_paths(facts.info)
}

bash_findings contains ["unresolved", "<the paths find hands to -exec>"] if {
	some _, facts in segment_facts
	facts.info.base == "find"
	some j, word in facts.words
	word in {"-exec", "-execdir", "-ok", "-okdir"}
	writes_given_paths(segment_info(array.slice(facts.words, j + 1, count(facts.words))))
}

bash_findings contains ["unresolved", "<the paths find -delete removes>"] if {
	some _, facts in segment_facts
	facts.info.base == "find"
	"-delete" in facts.words
}

# The unresolved rule only fires when the command names a crate or a Rust file somewhere, so a
# `> "$LOG"` beside a plain `cargo build -p` stays allowed. `.rs` counts only as an extension,
# not as the front of `.rsync` or `.rst`.
bash_command := object.get(object.get(input, "tool_input", {}), "command", "")

names_crates_or_rust if contains(bash_command, "crates")

names_crates_or_rust if {
	some i, piece in split(bash_command, ".rs")
	i > 0
	not rust_extension_continues(piece)
}

rust_extension_continues(piece) if {
	piece != ""
	contains(commands.shell_name_chars, substring(piece, 0, 1))
}

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	not read_only_tools[tool_name]
	rust_under_crates
	not proven

	decision := {
		"rule_id": "ER-EFFECTS-NO-RUST-EDIT-WITHOUT-FRIDA-PROOF",
		"severity": "HIGH",
		"reason": concat("", [
			block_reason,
			"\n\nTarget: ",
			file_path,
			"\nEvidence reader said: ",
			said,
		]),
	}
}

# A crate path is refused outright; a destination the guard cannot follow only when the command
# names a crate or a Rust file somewhere.
bash_refusals contains [said_for(kind), target] if {
	named := command_names_crates_or_rust
	some finding in bash_findings
	kind := finding[0]
	target := finding[1]
	finding_applies(kind, named)
	not proven_for(target)
}

finding_applies("crate", _)

finding_applies("unresolved", true)

command_names_crates_or_rust := true if {
	names_crates_or_rust
} else := false

said_for("crate") := written_crate_path_said

said_for("unresolved") := unresolved_write_said

written_crate_path_said := "\n\nThis is the Bash spelling of the same edit. A `sed -i`, a `> file`, a `tee`, a `cp` or a `git checkout --` writes the file exactly as the Edit tool does, and until 2026-09-22 this rule could not see any of them because it read `tool_input.file_path` and a Bash call has none.\n\nTarget: "

unresolved_write_said := "\n\nThis command writes to a destination the shell computes -- a variable, a substitution, a `cd` into one, or paths fed through `xargs` or `find` -- and it names a crate or a Rust file, so the guard cannot show the write misses `crates/**/*.rs`. On 2026-10-15 a subagent wrote two crate sources with `sed ... > $W/lib.rs`, `W` holding a crate's `src` directory, and the gate did not see it. Spell the destination as a literal path, or assign it from a literal in the same command (`W=/abs/path; ... > $W/lib.rs`), and the gate reads it like any other. A destination whose extension is literal and not `.rs` (`> $S/build.log`) is never refused.\n\nUnresolved destination: "

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	tool_name == "Bash"
	some refusal in bash_refusals

	decision := {
		"rule_id": "ER-EFFECTS-NO-RUST-EDIT-WITHOUT-FRIDA-PROOF",
		"severity": "HIGH",
		"reason": concat("", [
			block_reason,
			refusal[0],
			refusal[1],
			"\nEvidence reader said: ",
			said,
		]),
	}
}
