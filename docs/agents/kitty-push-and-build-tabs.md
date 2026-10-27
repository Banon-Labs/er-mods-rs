# Kitty tabs for pushes and long builds

## Every push gets a kitty tab that follows it (user request 2026-10-19)

An authorized `git push` runs in the background, because the pre-push hook runs the whole check
suite. Right after starting it, open a tab that shows it to the user:

```bash
python3 scripts/kitty-tail-push.py --log <the push task's output file> --title 'push <branch>'
```

The tab follows the log with `tail --pid` on the newest `git push` process (or `--pid <n>`), so it
lasts exactly as long as the push. It then prints `PUSH SUCCEEDED` or `PUSH FAILED` from git's own
output, waits 5 seconds, and closes. `--selftest` covers the verdict and the tab script's quoting.
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

## A long background build is tailed in a kitty tab (user directive 2026-10-19)

When the agent starts a long background build -- a duel or invasion build from
`scripts/er-weapon-build.py`, or any other pipeline run the user will want to follow -- it opens a
kitty tab that tails the task's output until the task's process exits, and closes 5 seconds
later. The agent opens it itself, in the same turn the task starts; the user runs nothing:

```bash
python3 scripts/kitty-tail-task.py --title 'duel build' \
    --file <task output file> --match er-weapon-build.py --match '--fight duel'
```

The tab opens with `--keep-focus`, refuses when the match names no process or several, and is the
user's view only: the agent still reads the outcome from the task's completion and its files. The
`tail-build-task` skill carries the details. A board regeneration (`gen-r3-weapon-boards.py`) writes
its progress to per-shard logs, so its tab is `python3 scripts/kitty-tail-task.py --boards --title
boards --file <task output file>`, which follows those. The Stop guard
`.cupcake/policies/claude/no_untailed_long_run.rego` halts a turn that backgrounded a run from its
signal's `LONG_RUNS` list (boards, weapon and DLL builds, the check suite, `git push`, ...) without
such a tab.
