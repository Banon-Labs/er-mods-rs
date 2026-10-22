# METADATA
# scope: package
# title: An me3 launch names its Windows binaries directory
# authors: ["er-quickload agents"]
# custom:
#   severity: HIGH
#   id: ER-EFFECTS-ME3-WINDOWS-BINARIES-DIR
#   description: >-
#     Hard block on an agent Bash command that runs `me3 launch` without the top-level option
#     `--windows-binaries-dir <dir>` standing before `launch`.
#
#     me3 0.11.0 looks for its own Windows binaries (me3-launcher.exe, me3_mod_host.dll) at the
#     cwd-relative path `target/x86_64-pc-windows-msvc/release` before it looks in
#     `~/.local/share/me3/windows-bin`. Measured 2026-10-22: launched from this repo's root, it
#     chose the repo's `target/x86_64-pc-windows-msvc/release/`, which holds the mod DLLs and no
#     me3-launcher.exe. Proton logged `err:steam:run_process Failed to create process
#     ...me3-launcher.exe: 2`, the container (`srt-bwrap`) exited 1, and me3 waited forever in
#     `wait_for_partner` on its monitor pipe without printing an error. The bd memory
#     `me3-launch-cwd-must-lack-rust-target-dir` already recorded the trap, and the agent walked
#     into it twice after that, so it is a deny here rather than a note.
#
#     Naming the directory takes the cwd out of the question, so the guard asks for the option
#     rather than for a particular cwd. `me3 --help` lists it under Configuration as "Path to me3
#     Windows binaries for Proton (Linux only)"; `me3 launch --help` does not, and
#     `me3 launch --windows-binaries-dir <dir>` exits with `unexpected argument`, so it has to
#     come before `launch` (both read from me3 0.11.0 on 2026-10-22).
#
#     Not judged, because nothing launches: `me3 --help`, `me3 launch --help`, `me3 profile
#     list`, `me3 info`, `me3 help launch`, and text that only mentions me3 -- a grep pattern, a
#     commit message, a `bd` body, a heredoc no shell reads.
#   routing:
#     required_events: ["PreToolUse"]
#     required_tools: ["Bash"]
package cupcake.policies.claude.me3_launch_needs_windows_binaries_dir

import rego.v1

import data.cupcake.system.commands

# The executed decomposition, not the raw command, for the reason `.cupcake/system/commands.rego`
# records: it follows `bash -c '...'` and `fish -c "..."` payloads, and it leaves a quoted mention
# of me3 (a grep pattern, a commit message, a `bd` body) without a command position.
executed_texts := commands.input_executed_texts

# Token operations rather than regexes throughout. The engine runs policies as wasm modules, and a
# regex in a rule crashed every policy at once on 2026-09-14 (bd
# `a-regex-in-a-rego-rule-can-crash-opa-wasm-and-silence-every-policy-2026-09-14`).

# A word with its shell quotes taken off its ends: `"$HOME/.local/bin/me3"` reads as the path.
unquoted(word) := trim(word, `"'`)

# The me3 binary by name or by any path ending in it: `me3`, `/home/banon/.local/bin/me3`,
# `$HOME/.local/bin/me3`, `~/.local/bin/me3`. A Windows `me3.exe` is left alone, since its
# option list has no `--windows-binaries-dir` ("Linux only" in `me3 --help`).
me3_word(word) if unquoted(word) == "me3"

me3_word(word) if endswith(unquoted(word), "/me3")

# The last path component of a word, for recognising a program however it is spelled.
basename(word) := parts[count(parts) - 1] if {
	parts := split(unquoted(word), "/")
}

unflagged_launch if {
	some text in executed_texts
	unflagged_launch_in(commands.command_slot_words(text))
}

# A newline ends a statement as `;` does, for the reason `no_whole_check_sh` records: the live
# engine collapses unquoted newlines to spaces, `opa test` does not, and a launch on the second
# line of a command must still stand in a command slot of its own.
unflagged_launch if {
	some text in executed_texts
	unflagged_launch_in(commands.command_slot_words(replace(replace(text, "\r", "\n"), "\n", " ; ")))
}

unflagged_launch_in(words) if {
	# Computed once per text, before the match iterates; see `data_heredoc_spans` in
	# `.cupcake/system/commands.rego` for what a per-candidate span test costs.
	spans := commands.data_heredoc_spans(words)
	some index, word in words
	me3_word(word)
	not commands.index_inside(spans, index)
	me3_program(words, index)
	args := me3_args(words, index)
	launch_at := launch_index(args)
	not help_requested(args)
	not names_windows_binaries_dir(args, launch_at)
}

# me3 is the program of its command: nothing but wrappers (`env`, `nohup`, `timeout 30`,
# `VAR=value`, a shell) stands between the command's start and it.
me3_program(words, index) if {
	not commands.operand_shaped(words, index)
	commands.word_in_command_slot(words, index)
}

# me3 handed to a launcher that runs the command after its own `--`: the repo's gamescope runs
# (`scripts/er-run-gamescope.sh`, the `gamescope_prefix` in the direct probe) put me3 there.
me3_program(words, index) if {
	index > 0
	words[index - 1] == "--"
	start := commands.command_slot_start(words, index)
	programs := [position |
		some position, _ in words
		position >= start
		position < index - 1
		not commands.command_slot_wrapper_at(words, position)
	]
	count(programs) > 0
	basename(words[min(programs)]) in {"gamescope", "systemd-run"}
}

# Where the command holding `index` ends: the next separator, or the end of the words.
command_end(words, index) := end if {
	separators := [position |
		some position, word in words
		position > index
		word == commands.command_slot_separator
	]
	end := min(array.concat(separators, [count(words)]))
}

# The words me3 was handed, up to the end of its command.
me3_args(words, index) := array.slice(words, index + 1, command_end(words, index))

# me3's global options that take a value (`me3 --help`, 0.11.0), so the word after one is that
# value and never the subcommand. `--crash-reporting` takes an optional `true` or `false`.
valued_global_option(word) if word in {"--config-file", "--profile-dir", "--steam-dir", "--windows-binaries-dir"}

option_value(args, k) if {
	k > 0
	valued_global_option(unquoted(args[k - 1]))
}

option_value(args, k) if {
	k > 0
	unquoted(args[k - 1]) == "--crash-reporting"
	unquoted(args[k]) in {"true", "false"}
}

# The subcommands `me3 --help` lists.
subcommand_word(word) if unquoted(word) in {"launch", "info", "profile", "help"}

# The index of `launch` when it is the subcommand: the first word naming a subcommand that is not
# an option's value. Read by name rather than as "the first positional word" so a quoted value
# with a space in it (`--steam-dir "/a b"`) cannot pass for the subcommand. Undefined for `me3
# profile list`, `me3 info` and `me3 help launch`, which launch nothing.
launch_index(args) := at if {
	subcommands := [k |
		some k, word in args
		subcommand_word(word)
		not option_value(args, k)
	]
	count(subcommands) > 0
	at := min(subcommands)
	unquoted(args[at]) == "launch"
}

# `me3 --help`, `me3 launch --help`, `-V`: clap prints and exits, nothing launches.
help_requested(args) if {
	some word in args
	unquoted(word) in {"-h", "--help", "-V", "--version"}
}

# `--windows-binaries-dir <dir>` before `launch`, with a value that is not empty.
names_windows_binaries_dir(args, launch_at) if {
	some k, word in args
	k + 1 < launch_at
	unquoted(word) == "--windows-binaries-dir"
	unquoted(args[k + 1]) != ""
}

# `--windows-binaries-dir=<dir>` before `launch`; clap reads the `=` spelling the same way.
names_windows_binaries_dir(args, launch_at) if {
	some k, word in args
	k < launch_at
	value := trim_prefix(unquoted(word), "--windows-binaries-dir=")
	value != unquoted(word)
	unquoted(value) != ""
}

block_reason := "🧁 Cupcake blocked an `me3 launch` that does not name its Windows binaries. me3 0.11.0 looks for me3-launcher.exe and me3_mod_host.dll in the cwd-relative `target/x86_64-pc-windows-msvc/release` before `~/.local/share/me3/windows-bin`, and this repo's root has that directory with no launcher in it. Measured 2026-10-22: Proton failed to create me3-launcher.exe (`err:steam:run_process ... : 2`), the container exited 1, and me3 hung in `wait_for_partner` without printing an error. Pass the directory as a top-level option, before `launch` (after it, me3 rejects it as an unexpected argument):\n\n    /home/banon/.local/bin/me3 --windows-binaries-dir /home/banon/.local/share/me3/windows-bin launch --auto-detect -p <profile>\n\n`python3 scripts/er-run-branch.py` and `~/Elden/launch.sh` launch from the game directory and are not affected; `me3_launch` in `scripts/me3-launch-lib.sh` passes the option itself."

deny contains decision if {
	input.hook_event_name == "PreToolUse"
	input.tool_name == "Bash"
	unflagged_launch

	decision := {
		"rule_id": "ER-EFFECTS-ME3-WINDOWS-BINARIES-DIR",
		"reason": block_reason,
		"severity": "HIGH",
	}
}
