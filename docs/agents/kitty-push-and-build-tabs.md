# Kitty splits for pushes and long builds

The helpers below open a split: a new kitty window in the agent session's own tab, found through
`$KITTY_WINDOW_ID` (user request 2026-11-07: "Can we do kitty split instead of tab by default?").
`--tab` opens a new tab instead. Without `$KITTY_WINDOW_ID` the split opens in the focused tab, and
the helper's output says so.

## Every push gets a kitty split that follows it (user request 2026-10-19)

An authorized `git push` runs in the background, because the pre-push hook runs the whole check
suite. Right after starting it, open a split that shows it to the user:

```bash
python3 scripts/kitty-tail-push.py --log <the push task's output file> --title 'push <branch>'
```

The split follows the log with `tail --pid` on the newest `git push` process (or `--pid <n>`), so it
lasts exactly as long as the push. It then prints `PUSH SUCCEEDED` or `PUSH FAILED` from git's own
output, waits 5 seconds, and closes. `--selftest` covers the verdict, the window script's quoting
and the split and tab launch options.

## A red stage is fixed while the rest of the suite runs (user directive 2026-11-25)

The split is the user's view; it wakes nobody. The suite prints `>>> stage <name> RED (exit <n>)
<secs>s <log>` the moment a stage fails, and the other stages can run for another hour. On
2026-11-25 the policy stage of a push went red at 1702 s and sat there until the push's completion
notification, after every other stage. The user's words: "It would be wise, and prudent, to not
wait until all checks pass before addressing any one of the failing reds in each future case". So
every background `git push`, hand-run pre-push hook or full `check.sh` also gets a `Monitor`, armed
in the same turn, with `timeout_ms` 1800000:

```bash
tail -n +1 --pid=<git push pid> -F <the push task's output file> \
    | grep --line-buffered -E 'RED|FAIL|error|REFUSED|rejected|exited with code' \
    | python3 /home/banon/projects/er-mods-rs/scripts/monitor-throttle.py 10
```

`kitty-tail-push.py` prints this exact command, with the pid filled in, after it opens the split
(`--monitor-command` prints only the command). `--pid` ends the Monitor when the push ends; without
it, stop the Monitor with TaskStop once the push is over. A Monitor lasts 30 minutes at most, so
re-arm it on its expiry notice while the suite still runs. When it reports a red stage, read that
stage's log (the path on the line) and start the fix at once. The Stop guard
`.cupcake/policies/claude/no_unwatched_red_stage.rego` (signal
`.cupcake/signals/last_assistant_unwatched_red_stage.sh`, logic and `--selftest` in
`scripts/cupcake_red_stage_watch.py`) halts a turn while such a task runs with no running Monitor
that follows its output file and passes `RED`, and names the command to arm.
Close Elden Ring before pushing: check.sh refuses while it runs. When the game up is the run that
stamped the tip, close it with `python3 scripts/er-teardown.py --reason=stamped-<one-token-why>` as
the whole command (a trailing `| tail -<n>` is allowed), which the teardown guard admits only while
the `stamped_run` signal (`scripts/cupcake_stamped_run.py`) proves that run's own log carries
evidence `scripts/check-runtime-evidence.sh` accepts for the tip.

The user's `authorize <sha>` reply pays for the teardown its push needs, whatever the run up was
for (user directive 2026-10-22: "to authorize a push sha is to authorize a teardown"). Do not hand
the closing back to the user: while that reply is still their last message, close the game with
`python3 scripts/er-teardown.py --reason=push-<one-token-why>` as the whole command, then push. The
teardown guard admits it only while the `push_teardown` signal (`scripts/cupcake_push_teardown.py`)
reads the reply as an unspent `authorize <sha>` for a local branch tip.

If the agent forgets, the push does not burn the reply (fixed after 2026-10-27, when an authorized
push of 6e313ae7 died on "check.sh: REFUSED -- Elden Ring is running" with the agent's own
stamping run still up, and the reply counted as spent anyway). The pre-push hook now does two
things through `scripts/er-pre-push-clear.py`:

- Just before the check suite, if a game is up, the hook closes it itself with
  `er-teardown.py --reason=push-<tip12>` -- but only when the game is a live `er-run-branch.py`
  launch (the newest `br-*` run still has its `run.json` and a live launcher) and this push holds
  a fresh `authorize <sha>` record in `.cupcake/state/push-authorizations.jsonl`. A game the user
  started themselves, or a push the user typed in their own terminal, is never closed by the hook.
- When the hook refuses a push for something that is not about the pushed code -- a game it may
  not close, a dirty working tree, another push holding the gate tree, missing runtime evidence --
  it marks the reply refunded. The push guard and the `push-` teardown both treat a refunded reply
  as unspent, so the agent clears the problem and runs the same `git push` again without asking
  the user for a new reply. A push that reached the suite and failed it still spends the reply.

## A long background build is tailed in a kitty split (user directive 2026-10-19)

When the agent starts a long background build -- a duel or invasion build from
`scripts/er-weapon-build.py`, or any other pipeline run the user will want to follow -- it opens a
kitty split that tails the task's output until the task's process exits, and closes 5 seconds
later. The agent opens it itself, in the same turn the task starts; the user runs nothing:

```bash
python3 scripts/kitty-tail-task.py --title 'duel build' \
    --file <task output file> --match er-weapon-build.py --match '--fight duel'
```

The split opens with `--keep-focus`, refuses when the match names no process or several, and is the
user's view only: the agent still reads the outcome from the task's completion and its files. The
`tail-build-task` skill carries the details. A board regeneration (`gen-r3-weapon-boards.py`) writes
its progress to per-shard logs, so its split is `python3 scripts/kitty-tail-task.py --boards --title
boards --file <task output file>`, which follows those. The Stop guard
`.cupcake/policies/claude/no_untailed_long_run.rego` halts a turn that backgrounded a run from its
signal's `LONG_RUNS` list (boards, weapon and DLL builds, the check suite, `git push`, ...) without
such a split or tab.
