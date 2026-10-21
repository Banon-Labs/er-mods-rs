# METADATA
# scope: package
# title: Cap a parallel fan-out at 4 workers unless it runs in a memory-capped scope
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-CAP-PARALLEL-FANOUT
#   description: >-
#     Hard block on a Bash command that starts more than 4 parallel workers through
#     `xargs -P N` / `--max-procs N` or GNU `parallel -j N` / `--jobs N` / `-P N`, unless
#     the fan-out runs inside `systemd-run --user --scope -p MemoryMax=<size>`.
#     Measured 2026-10-15: systemd-oomd killed the user's whole kitty terminal scope twice
#     in eight minutes (09:56:26, 47.2G memory peak and 35.1G swap peak; 10:03:40, 166
#     processes, 48.3G and 30G), memory and swap both past 90% of 62 GB. Each kill came
#     35-41 seconds after a subagent backgrounded `printf '%s\n' work/*/$k.ll | xargs -n
#     100 -P 15 python3 .../pos_slice.py`, then `ls work/*/orig.ll | xargs -n 200 -P 8
#     python3 $P/pos_slice.py`. Everything an agent launches lives in the terminal's
#     scope, so each kill took the Claude session, every subagent and the user's Elden
#     Ring game with it. A scope with MemoryMax puts the job in a cgroup of its own, and
#     the kernel kills the job instead of the terminal.
#
#     Counted as more than 4: `-P 0` / `-j 0` (no limit), a percentage, any count that is
#     not a literal number (`$(nproc)`, `$N`), and `parallel` with no job option at all,
#     whose default is one job per core.
#
#     Exempt by shape, as in the other guards: a single bd command or a git commit
#     message that names the command, and quoted text that is not run. Here that falls
#     out of the reading itself -- only an `xargs` or `parallel` standing in a command
#     slot of an executed text counts, and a quoted operand never stands in one.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.cap_parallel_fanout

import rego.v1

import data.cupcake.system.commands

command := object.get(input.tool_input, "command", "")

# Normalization and quote handling come from `.cupcake/system/commands.rego` rather than
# from a private copy of the strip `require_jobs_er_builds_pvp` does. Two reasons. That
# strip deletes quoted spans, and the allowed form here is a quoted `bash -c "..."`
# payload whose fan-out has to be read, not deleted; `input_executed_texts` hands every
# shell payload over as an executed text of its own, three levels deep. And a regex in
# a rule crashed every policy at once in the wasm runtime on 2026-09-14 (bd
# `a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14`), so
# this file is written in token operations and calls no regex at all.
#
# Newlines become separators, as in `require_scoped_cargo`: the live engine collapses
# unquoted newlines before any policy runs, `opa test` does not.
slot_words(text) := commands.command_slot_words(replace(replace(replace(text, "\t", " "), "\r", "\n"), "\n", " ; "))

# A word with the quote and paren characters a shell word can carry at its edges removed.
bare(word) := trim(word, "\"'`)")

program_name(word) := name if {
	parts := split(trim_left(bare(word), "\\"), "/")
	name := parts[count(parts) - 1]
}

fanout_tools := {"xargs", "parallel"}

names_a_fanout_tool(text) if contains(text, "xargs")

names_a_fanout_tool(text) if contains(text, "parallel")

# Cheap substring test before the split `program_name` does, because this runs once per
# word of every executed text that mentions either tool.
fanout_word(word) if contains(word, "xargs")

fanout_word(word) if contains(word, "parallel")

# Words that may stand in front of the program without being it, beyond the shared set in
# `commands.command_slot_wrapper`. `systemd-run` is one on purpose: `systemd-run --user
# --scope xargs -P 16 ...` with no MemoryMax has to be read as the fan-out it is.
extra_slot_prefixes := {"{", "!", "time", "ionice", "chrt", "systemd-run"}

# systemd-run options whose value is the next word, so `--unit fanout xargs ...` does not
# read `fanout` as the program.
systemd_run_argument_options := {
	"-p", "--property", "-u", "--unit", "--slice", "-E", "--setenv", "--description",
	"--working-directory", "-M", "--machine", "-H", "--host", "--uid", "--gid", "--nice",
}

slot_prefix(words, position) if commands.command_slot_wrapper_at(words, position)

slot_prefix(words, position) if program_name(words[position]) in extra_slot_prefixes

slot_prefix(words, position) if {
	position > 0
	bare(words[position - 1]) in systemd_run_argument_options
}

in_command_slot(words, index) if {
	start := commands.command_slot_start(words, index)
	count([position |
		some position, _ in words
		position >= start
		position < index
		not slot_prefix(words, position)
	]) == 0
}

# `command -v parallel` asks whether the tool exists; it runs nothing.
existence_check(words, index) if {
	index > 1
	bare(words[index - 1]) in {"-v", "-V"}
	program_name(words[index - 2]) == "command"
}

# The words of one invocation, up to the next command separator.
invocation_args(words, index) := args if {
	ends := [position |
		some position, word in words
		position > index
		word == commands.command_slot_separator
	]
	end := min(array.concat(ends, [count(words)]))
	args := array.slice(words, index + 1, end)
}

# Option grammar, from the installed tools (2026-10-15): GNU findutils xargs 4.11.0 and GNU
# parallel 20260922. Both stop reading options at the first word that is neither an option
# nor an option's value -- getopt for xargs, Getopt::Long `bundling` + `require_order` for
# parallel -- so an `-P` or `-j` after that word belongs to the command being fanned out
# (`xargs grep -P ...`, `parallel make -j 8 ...`) and is not read as a worker count.
flag_letters := {
	"xargs": "0oprtx",
	"parallel": "0TYgkmopqrtuvxX",
}

argument_letters := {
	"xargs": {"a", "d", "E", "I", "L", "n", "P", "s"},
	"parallel": {"a", "B", "C", "d", "D", "E", "H", "I", "j", "J", "L", "n", "N", "P", "s", "S", "U", "W"},
}

job_letters := {
	"xargs": {"P"},
	"parallel": {"j", "P"},
}

argument_long_options := {
	"xargs": {"--arg-file", "--delimiter", "--max-args", "--max-chars", "--process-slot-var"},
	"parallel": {
		"--arg-file", "--argfile", "--arg-file-sep", "--arg-sep", "--basefile", "--bf",
		"--block", "--block-size", "--block-timeout", "--bt", "--colsep", "--col-sep",
		"--delay", "--delimiter", "--env", "--filter", "--group-by", "--halt", "--header",
		"--jl", "--joblog", "--limit", "--load", "--match", "--max-args", "--max-chars",
		"--max-lines", "--max-replace-args", "--memfree", "--memsuspend", "--nice",
		"--process-slot-var", "--profile", "--recend", "--recstart", "--res", "--result",
		"--results", "--retries", "--return", "--rpl", "--semaphore-name",
		"--semaphore-timeout", "--shard", "--ssh", "--ssh-delay", "--sshdelay", "--sshlogin",
		"--sshloginfile", "--slf", "--tag-string", "--tagstring", "--tempdir", "--template",
		"--term-seq", "--termseq", "--tf", "--timeout", "--tmpdir", "--tmpl",
		"--transfer-file", "--transferfile", "--wd", "--work-dir", "--workdir",
	},
}

# Asking parallel about itself starts no jobs.
parallel_information_options := {
	"--help", "-h", "--version", "-V", "--citation", "--bibtex", "--minversion",
	"--number-of-cpus", "--number-of-cores", "--number-of-sockets", "--number-of-threads",
}

# `--max-procs` and the abbreviations getopt accepts for it (`--max-p` is already unique),
# plus parallel's other spellings of the same option.
job_long_option(_, word) if {
	startswith(word, "--max-p")
	startswith("--max-procs", word)
}

job_long_option("parallel", word) if word in {"--jobs", "--maxprocs"}

# What is left of a short-option cluster once its leading no-value flags are read off:
# `-0P8` leaves `P8`, `-tP` leaves `P`, `-P` leaves `P`.
short_cluster_rest(tool, word) := trim_left(trim_prefix(word, "-"), flag_letters[tool]) if {
	startswith(word, "-")
	not startswith(word, "--")
	word != "-"
}

takes_argument(tool, word) if word in argument_long_options[tool]

takes_argument(tool, word) if job_long_option(tool, word)

takes_argument(tool, word) if short_cluster_rest(tool, word) in argument_letters[tool]

redirection(word) if startswith(word, "<")

redirection(word) if contains(word, ">")

redirection_operator(word) if endswith(word, "<")

redirection_operator(word) if endswith(word, ">")

option_argument(tool, args, k) if {
	k > 0
	takes_argument(tool, bare(args[k - 1]))
}

option_argument(_, args, k) if {
	k > 0
	redirection_operator(bare(args[k - 1]))
}

options_end(_, args, k) if bare(args[k]) == "--"

options_end(tool, args, k) if {
	word := bare(args[k])
	word != ""
	not startswith(word, "-")
	not redirection(word)
	not option_argument(tool, args, k)
}

option_region(tool, args) := array.slice(args, 0, end) if {
	ends := [k |
		some k, _ in args
		options_end(tool, args, k)
	]
	end := min(array.concat(ends, [count(args)]))
}

following(region, k) := word if {
	k + 1 < count(region)
	word := bare(region[k + 1])
} else := ""

attached_or_following(region, k, "") := following(region, k)

attached_or_following(_, _, attached) := attached if attached != ""

# The worker count one option word sets, in each spelling: `--max-procs 8`,
# `--max-procs=8`, `-P 8`, `-P8`, `-0P8`. A missing value reads as "" and is refused.
job_value(tool, region, k, word) := following(region, k) if job_long_option(tool, word)

job_value(tool, _, _, word) := value if {
	parts := split(word, "=")
	count(parts) > 1
	job_long_option(tool, parts[0])
	value := concat("=", array.slice(parts, 1, count(parts)))
}

job_value(tool, region, k, word) := value if {
	rest := short_cluster_rest(tool, word)
	rest != ""
	letter := substring(rest, 0, 1)
	letter in job_letters[tool]
	value := attached_or_following(region, k, trim_prefix(rest, letter))
}

job_values(tool, region) := {value |
	some k, raw in region
	value := job_value(tool, region, k, bare(raw))
}

# A literal count of 1 to 4. Everything else is refused: 0 (no limit for both tools), a
# percentage or `+N`/`-N` (relative to the core count), and anything the shell computes
# (`$(nproc)` is 16 here).
small_worker_count(value) if trim_left(value, "0") in {"1", "2", "3", "4"}

excess_workers(tool, region) if {
	some value in job_values(tool, region)
	not small_worker_count(value)
}

# parallel with no job option runs one job per core, 16 on this box.
excess_workers("parallel", region) if {
	count(job_values("parallel", region)) == 0
	not information_only(region)
}

information_only(region) if {
	some raw in region
	bare(raw) in parallel_information_options
}

# Every fan-out that asks for more than 4 workers, as [executed text, word index].
#
# A heredoc body no shell reads is data, not a run (`cat > run.sh <<'EOF' ... EOF`, or a
# commit message passed through one); the span computation is the shared one
# `no_whole_check_sh` and `require_scoped_cargo` use, computed once per text.
excess_fanouts contains [text, index] if {
	some text in commands.input_executed_texts
	names_a_fanout_tool(text)
	words := slot_words(text)
	spans := commands.data_heredoc_spans(words)
	some index, word in words
	fanout_word(word)
	tool := program_name(word)
	tool in fanout_tools
	not commands.index_inside(spans, index)
	in_command_slot(words, index)
	not existence_check(words, index)
	excess_workers(tool, option_region(tool, invocation_args(words, index)))
}

# The capped form, read where it is written: a `systemd-run` in a command slot whose own
# options carry `--user`, `--scope` and a non-empty `MemoryMax`, and whose command -- up
# to the next separator, quoted payload included -- contains the fan-out. Everything that
# command starts, a `bash -c` payload and its children included, stays in that scope.
#
# Containment is tested on flattened words rather than by locating the payload, because
# the executed texts arrive as a set with no record of which statement each came from.
# The view used here blanks quoted spans without the shared code's command-substitution
# refusal, so a capped `bash -c "... $(ls) ... | xargs -P 12 ..."` still reads as one
# statement. Two limits follow, both needing a command written against the guard rather
# than one written by accident: a `$(...)` inside a double-quoted payload is expanded by
# the outer shell, outside the scope, and an uncapped fan-out written word for word the
# same as one inside a capped statement of the same command is taken as that one.
memory_capped_scope(options) if {
	words := [bare(word) | some word in options]
	count({word | some word in words; word in {"--user", "--scope"}}) == 2
	some k, _ in words
	caps_memory(property_assignment(words, k))
}

property_assignment(words, k) := words[k + 1] if words[k] in {"-p", "--property"}

property_assignment(words, k) := trim_prefix(words[k], "--property=") if startswith(words[k], "--property=")

property_assignment(words, k) := trim_prefix(words[k], "-p") if {
	startswith(words[k], "-p")
	not startswith(words[k], "--")
	words[k] != "-p"
}

caps_memory(assignment) if {
	startswith(assignment, "MemoryMax=")
	size := trim_prefix(assignment, "MemoryMax=")
	size != ""
	lower(size) != "infinity"
}

systemd_run_command_start(args, k) if bare(args[k]) == "--"

systemd_run_command_start(args, k) if {
	word := bare(args[k])
	word != ""
	not startswith(word, "-")
	not after_systemd_run_argument_option(args, k)
}

after_systemd_run_argument_option(args, k) if {
	k > 0
	bare(args[k - 1]) in systemd_run_argument_options
}

systemd_run_options(args) := array.slice(args, 0, end) if {
	ends := [k |
		some k, _ in args
		systemd_run_command_start(args, k)
	]
	end := min(array.concat(ends, [count(args)]))
}

relaxed_blank(text) := out if {
	out := commands.blank_spans(text, `"`, "'")
} else := out if {
	out := commands.blank_spans(text, "'", `"`)
} else := text

cap_view_words(text) := slot_words(replace(replace(relaxed_blank(commands.escaped_quotes_stripped(text)), "'", " "), `"`, " "))

flat_words(words) := concat(" ", array.concat(array.concat([""], words), [""]))

flat_text(text) := flat_words([word |
	some word in split(replace(text, "\t", " "), " ")
	word != ""
])

flattened(words) := flat_text(commands.blanked_anchors(concat(" ", words)))

capped(text, index) if {
	words := slot_words(text)
	site := flattened(array.concat([words[index]], invocation_args(words, index)))
	some outer in commands.input_executed_texts
	contains(outer, "systemd-run")
	view := cap_view_words(outer)
	some position, word in view
	endswith(word, "systemd-run")
	program_name(word) == "systemd-run"
	in_command_slot(view, position)
	args := invocation_args(view, position)
	memory_capped_scope(systemd_run_options(args))
	contains(flattened(args), site)
}

uncapped_fanout if {
	some site in excess_fanouts
	not capped(site[0], site[1])
}

block_reason := "🧁 Cupcake blocked a fan-out of more than 4 parallel workers outside a memory-capped scope. On 2026-10-15 two of them (`xargs -P 15`, then `xargs -P 8`) pushed memory and swap past 90% of 62 GB, and systemd-oomd killed the whole kitty terminal scope both times, taking the Claude session, every subagent and the user's game with it.\nEverything an agent starts lives in that terminal scope, so an uncapped fan-out is paid for by the terminal; inside a scope with MemoryMax the kernel kills the job instead.\n\nAllowed: at most 4 workers written as a literal number, `xargs -P 4 ...` or `parallel -j 4 ...`; or any count inside a capped scope, `systemd-run --user --scope -p MemoryMax=8G -- bash -c \"... | xargs -P 12 ...\"`. Counted as more than 4: `-P 0` / `-j 0` (no limit), a percentage, `$(nproc)` or any other computed count, and `parallel` with no `-j` (one job per core)."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	uncapped_fanout

	decision := {
		"rule_id": "ER-EFFECTS-CAP-PARALLEL-FANOUT",
		"severity": "HIGH",
		"reason": concat("", [block_reason, "\n\nSource: ", command]),
	}
}
