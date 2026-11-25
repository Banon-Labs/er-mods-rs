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

## "CI is still running" needs a watcher of those checks (user correction 2026-11-25)

The `CI STATE (measured, not remembered)` line that each prompt injects is one read of
`gh pr checks`, and so is a `gh pr checks <n>` call. Neither one follows CI. A closing message may
say that CI is running, pending or queued, or that a CI job has not finished, only while a
background task of the session is watching that pull request's checks. The watcher's output is
tailed in a split, and its task id is named in the closing message:

```bash
gh pr checks <n> --watch --interval 30 > <log> 2>&1      # run_in_background; or: gh run watch <run-id>
python3 scripts/kitty-tail-task.py --title 'CI: PR #<n>' --file <log> --match 'gh pr checks'
```

This came from a session that closed with "CI on GitHub is still running; `compiles` and
`cargo-test` hadn't finished at last check." after one read of that line. Nothing was watching the
checks, and no split was open. The user's reply: "I don't believe you because your rego policy that
told you to open a kitty tab/split to tail the output didn't run". Remote CI is not a local task, so
none of the background-task guards had anything to check. The Stop guard
`.cupcake/policies/claude/no_unwatched_ci_claim.rego` now halts such a claim when no live
`gh pr checks --watch`, `gh run watch` or until/while polling loop is following it. Its signal is
`.cupcake/signals/last_assistant_ci_running_claim.sh`, and the classifier lives in
`scripts/cupcake_ci_running_claim.py` (`--selftest`). Quoted text is skipped, and so is a report
that CI finished, passing or failing. `no_untailed_long_run` counts both watchers as long runs, so
a watcher without its split halts as well.
