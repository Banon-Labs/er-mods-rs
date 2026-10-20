---
name: tail-build-task
description: Show the user a long-running background task in a kitty tab that tails its real output until the task finishes and closes itself 5 seconds later -- a board regeneration (gen-r3-weapon-boards.py, whose progress is in per-shard logs), a duel or invasion build from er-weapon-build.py, er-build-dlls.sh, er-builds-pvp.py, the check suite, or any other build/pipeline run. Use right after starting such a task in the background, and whenever the user asks to watch, follow or tail one. The Stop guard no_untailed_long_run halts a turn that skips it.
---

# Tail a background task in a kitty tab

One command, run by the agent in the same turn the task starts, never handed to the user:

```bash
python3 scripts/kitty-tail-task.py --title 'duel build' \
    --file <the task's output file> --match er-weapon-build.py --match '--fight duel'
```

## Board regeneration: follow the shard logs, not the wrapper

`gen-r3-weapon-boards.py` prints four setup lines and nothing else. Every progress line
(`shard 0: 60/183 Iron Cleaver ok 48s`) goes to one `<cache>/shard-<n>.log` per worker, where
`<cache>` is the `cache /home/banon/.cache/er-build-planner/r3-boards/<hash>` line the wrapper
prints first. A tab on the wrapper's output alone shows nothing of the run, and the guard does not
count it. Use `--boards`, which finds the wrapper process, reads its `cache` line and follows the
wrapper output plus every shard log:

```bash
python3 scripts/kitty-tail-task.py --boards --title boards --file <the task's output file>
```

By hand, that is one `--file` per `<cache>/shard-<n>.log` (n from 0 to `--jobs` less one) plus
`--pid` of the wrapper. A shard log that does not exist yet is followed anyway; `tail -F` picks it
up when the shard opens it.

## The details

- `--file` is where the task writes: the file its own redirect names when both streams go there
  (`> <scratchpad>/build.out 2>&1`), or the harness's `tasks/<id>.output` when it has no redirect.
  Repeat it to follow several files in one tab.
- `--match` substrings pick the task's process by command line; repeat until exactly one live
  process matches (the shell that launched it is dropped as its ancestor). When several still
  match -- two sessions running the same script -- the one whose stdout is the first `--file` is
  kept, then the one working in this checkout (`--cwd` names another). `--pid` names it directly.
- `er-weapon-build.py` colours its output even into a file, so the tab shows it in colour;
  `weapon-build.log` stays plain, and the in-place redraw stays off when stdout is a file.
- A push uses its own tab: `python3 scripts/kitty-tail-push.py --log <the push's output file>`.
- The tab runs `tail -n +1 -F --pid=<pid> <files>; sleep 5`, opens with `--keep-focus`, and closes
  on its own 5 seconds after the process exits. There is nothing for the user to press.
- It refuses rather than guesses when no process or several match; `--dry-run` shows the
  `kitty @ launch` it would send, and `--selftest` checks the tail, the tie-breaks and `--boards`.

## The guard

`.cupcake/policies/claude/no_untailed_long_run.rego` (signal
`.cupcake/signals/last_assistant_untailed_long_run.sh`) halts a turn that backgrounded a run from
its `LONG_RUNS` list and opened no tab following that run's real output after it. The halt names
the command to run. If the tab command itself fails, say so in the closing message with its error.

Open the tab after the task is running (the match needs the live process). A tab is the user's
view only: the agent still learns the outcome from the task's completion notification and the
files, never by reading the tab.
