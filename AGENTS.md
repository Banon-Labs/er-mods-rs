# Agent Instructions

This project uses **bd** (beads) for issue tracking. **Invoke the real binary directly at `$HOME/.local/bin/bd`** -- do NOT use the bare `bd` command. The bare `bd` is a shell guard *function* (from the interactive shell snapshot) that errors with `bd guard error: unable to locate real bd binary` unless `BD_REAL_BIN` is exported, and non-interactive/agent shells do not get that function or env var. The local-bin path is the same ELF binary the guard would exec, so calling it directly works across current-user home directories. Run `$HOME/.local/bin/bd prime` for the bounded workflow context (memory search index + newest memories + ready queue); the project rules themselves live in this file.

## Read on demand

The rest of the project rules live in `docs/agents/`. Nothing there is loaded automatically: read
the file that matches the work before starting it.

| Before you ... | read |
|---|---|
| push, or start a long background build, board run or check suite | `docs/agents/kitty-push-and-build-tabs.md` |
| touch a live game: probe, hook, watchpoint, "who wrote this field" | `docs/agents/frida.md` |
| launch Elden Ring: autoload, Steam preflight, user-inspection runs | `docs/agents/runtime-launch.md` |
| claim a runtime or visual result, add an oracle, set a probe timeout | `docs/agents/runtime-evidence.md` |
| touch the supported Seamless Co-op version or its archive | `docs/agents/seamless.md` |
| any RE lookup: names, decompiles, struct layouts, which image an address is on | `docs/agents/ghidra.md` |
| disassemble, decode in-process, hook mid-function, attach or inject | `docs/agents/disassembly-and-hooking.md` |
| networking RE (network params, session pointer paths) | `docs/agents/networking-cheat-table.md` |
| work on Load Character / repeat-load, or name the quit-menu rows | `docs/agents/no-compromises.md` |
| build, test, run `check.sh`, or brief a subagent on which checks to run | `docs/agents/build-and-test.md` |
| write Rust, Python or comments, or draw an in-game overlay | `docs/agents/conventions.md` |

## A command for the user goes into a kitty tab, never into the reply (user directive 2026-10-13)

When the agent wants the user to run a command, it does not paste a bash block into its
message. It opens one kitty tab per command block, focused, with the command typed in but not
submitted, under a `#` comment block that says why it should be run:

```bash
python3 scripts/kitty-user-command.py --title 'push loading PR' \
    --why 'The agent may not push; this publishes fix/load-character-from-file.' \
    -- 'git -C /home/banon/projects/er-mods-rs/.worktrees/pr-x push -u origin fix/x'
```

The reply then says in prose which tabs it opened and what each one does. The helper wraps
the command as `bash -c '...'` because the tabs run fish, and it finds kitty through
`$KITTY_LISTEN_ON` because `kitty.conf` uses a random port (`listen_on tcp:127.0.0.1:0`).
`--dry-run` shows what it would send; `--selftest` proves the quoting in bash and fish;
`--live-selftest` sends that quoting through a real fish tab and checks what fish ran. Run the
live one after touching the helper: on 2026-10-13 the offline test passed while keystroke-typed
text reached fish mangled, which is why everything now goes in as a bracketed paste. The same day
two handed-over tabs came up as bare prompts, because the text was sent before fish drew its first
prompt and its startup ate it; the helper now waits for kitty's `at_prompt` and exits non-zero
unless the comments and the command are on screen, so a tab it reports is a tab with the command
in it.
A long command goes in a file passed as `--command-file <path>`: a `git push && gh pr
create ... --body-file ...` chain inline ran the policy engine out of memory on 2026-10-13.
The Stop hook refuses a closing message that carries a shell code block.

A push and a long background build each also get a kitty tab: `docs/agents/kitty-push-and-build-tabs.md`.

## A command written for the user is spelled absolutely

Every command example in this file, and in every Cupcake block message, is written
repo-relative because the agent's shell is already in the repo root. **A command the
user will read is a different artifact and must carry the full path** -- inside the kitty
tab above as much as anywhere -- because their shell is somewhere else and
`python3 scripts/er-teardown.py` names nothing there:

```bash
python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py    # hand the user THIS
python3 scripts/er-teardown.py                                    # run it yourself as THIS
```

"Run it from `/home/banon/projects/er-mods-rs`" is not a substitute -- it makes the
user do the joining, and the joining is the part that was wrong. This is the same rule
the global instructions already state for artifact paths, applied to commands: absolute,
clickable, pasteable as-is. The habit came from somewhere executable and was fixed
there too -- `bash_no_python_file_write.rego` used to admit ONLY the repo-relative
spelling and deny this repo's own absolute path, so the guard taught the relative form
and then the relative form reached the user (measured 2026-09-21, on a teardown the
user asked for twice and could not run).

## Quick Reference

```bash
$HOME/.local/bin/bd ready              # Find available work
$HOME/.local/bin/bd show <id>          # View issue details
$HOME/.local/bin/bd update <id> --claim  # Claim work atomically
$HOME/.local/bin/bd close <id>         # Complete work
$HOME/.local/bin/bd dolt push          # Push beads data to remote
```

For task startup in this repo, read relevant `bd` memories (`$HOME/.local/bin/bd memories <topic>` and `$HOME/.local/bin/bd recall <key>`) before broad source inspection or implementation. Treat memories as the first-pass continuation context; do not discover them midstream after choosing an approach.

## Elden Ring launch, bundle and save boundaries

Do not launch Elden Ring through Steam from agent workflows. Forbidden launch forms include `steam -applaunch 1245620`, `steam://run/1245620`, `steam://rungameid/1245620`, and `xdg-open` or similar wrappers around those URLs. Do not launch `start_protected_game.exe` directly or through Proton/Wine/Steam; that is the protected/EAC launcher, not an approved agent runtime target. Process detection of stale `start_protected_game.exe` is allowed, but launching it is not. Runtime work must use only an approved, explicitly gated direct/offline `eldenring.exe` probe path.

Do not bundle `ersc.dll`. Seamless Co-op is a compatibility target, but this repo must not copy, move, archive, release-package, or stage `SeamlessCoop/ersc.dll` into me3/product release artifacts or repo `target/` bundles. For er-net-effects ME3 profiles, still include a `[[natives]]` entry that references the game-installed Seamless Co-op DLL under the Elden Ring install's `Game/SeamlessCoop/ersc.dll`; this is a runtime profile reference, not bundling or staging the DLL. **Resolve that install path, do not copy one from these notes.** This machine now runs a NATIVE LINUX Steam install -- `$HOME/.local/share/Steam/steamapps/common/ELDEN RING/Game/` -- which is what every `.me3` profile in `~/Elden/` actually references, and `$HOME/Elden/launch.sh` derives from `ME3_STEAM_DIR`. The `C:\SteamLibrary\...` path this line used to name belongs to the retired WSL2 setup: there is no `/mnt/c` here (the Windows partitions mount as `/mnt/win-c`, `/mnt/win-d`), so every such path silently resolves to nothing and a `find` over it returns empty rather than erroring -- which reads as "the file is missing" instead of "you looked in the wrong place".

Do not COMMIT game-derived binaries either (user directive 2026-07-02): no extracted or transformed game assets (`.gfx`, `.dcx`, `.bnd`, `.tpf`, `.sl2`, texture/font payloads) as repo files, including test fixtures. Version FINGERPRINTS (length + FNV/sha constants) and deterministic generators instead; tests that need real asset bytes read them from the local extraction corpus (env-overridable root, e.g. `ER_GFX_CORPUS_ROOT` in `crates/er-gfx/tests/common/mod.rs`) and SKIP when it is absent. Large embedded byte arrays in `.rs` sources are the same problem in different clothing -- prefer runtime derivation from the game's own in-memory data (see `er_gfx::title_05_000`) or structured edit tables over byte dumps.

User-provided or configured save files are read-only sources, including repo/local `save-files` inputs and arbitrary picked `.sl2`/`.co2` paths. Runtime/profile-switch writes must go only to the game-owned active default save copy/staged tree. The narrow exception is the live game-owned `%APPDATA%/EldenRing/<steamid>/ER0000.{sl2,co2}` path itself, which must remain writable because Elden Ring owns it and may crash or fail if it is forced read-only.

## Never instruct the user in their own game (user directive 2026-09-15)

When a runtime measurement needs something to happen in game, say WHAT EVIDENCE IS NEEDED and stop.
Do not spell out the steps, the order, or the menus. The user has played this game for years, they
taught this repo how Seamless works, and a procedure recited back at them is both useless and
wrong often enough to cost real time.

The failure this was written from, 2026-09-15: the agent wrote "use Dried Fingers, then rehost".
That order is impossible -- Seamless's Dried Fingers cannot be activated in a session where it has
already been used, so a fresh session must come FIRST and the item second. The user had already
said so twice in the same session ("I can't activate dried finger unless I have a new session with
no prior activation"). The agent recited a sequence it had been corrected on, in the wrong order,
to the person who corrected it.

The rule, concretely:

- Ask for an OUTCOME: "a fresh session with the item used in it", "a host advertising from a world
  the item is active in". Never a button sequence, never "open X then cancel then use Y".
- The reason the agent wants it may be stated once, briefly, because that is what lets the user
  choose a better route than the one the agent had in mind.
- If the agent genuinely needs a specific in-game action performed a specific way, driving it is
  an option the agent has and should weigh; asking for it is also allowed since the 2026-07-22
  order was removed. What is still wrong either way is reciting a sequence back to the user in an
  order they have already corrected.
- Game facts the user states are ground truth about their game and are not re-derived or
  re-ordered. Write them down (`bd remember`) rather than paraphrasing them back later.

The 2026-07-22 standing order that the agent drive every in-game input was REMOVED by the user on
2026-09-23. It said that asking the user to perform an input was an instruction-following failure
and that every runtime test had to be agent-driven end to end. It no longer holds: pinning
`er-input-harness` to drive a menu walk is now a choice to justify per run, not an obligation, and
the user playing their own game is an ordinary way for a run to proceed. The rule above it still
stands and is the one that governs -- ask for an OUTCOME, never recite a button sequence.

## User steering and prose

User steering is not evidence. When the user proposes a concrete technical hypothesis or fallback during RE/runtime work, treat it as a lead to verify, not as ground truth and not as permission to skip research. Before implementing a user-steered objective claim, inspect the current static/runtime evidence that could confirm or falsify it, state the verified delta in the work artifacts/logs, and only then choose the next code change. If the evidence contradicts part of the steering, preserve the valid intent but correct the mechanism instead of reflexively agreeing.

### Prose-to-knowledge gate

If and only if the agent's recent user-facing prose referred to an entity, identifier, plan node, claim, or term as meaningful but the agent did not have enough information to communicate what that prose meant, the agent must say plainly that it does not know what the referenced thing is **before** starting any search, lookup, or inspection to clarify it. The admission must be user-visible in the same turn and precede every relevant tool call. Do not replace it with a tentative definition, a plan built on the unknown term, or search narration. If that admission was not made first, do not perform the clarifying lookup in that turn.

## Reusable Tooling / Hard-Coded Path Corrections

Persistent user directive (2026-07-17): when a tool, script, helper, or documented workflow fails because of a hard-coded local path, username, machine layout, or one-off assumption, fix the reusable tool/instruction at the point of failure before continuing the one-off task. Prefer env-overridable, current-user-aware defaults (`$HOME`, discovered repo root, explicit `*_DIR`/`*_BIN` overrides, bounded known-location fallbacks) over `/home/banon`, `/home/choza`, or other user-specific literals. Do not paper over the failure by running an ad-hoc command that only works in the current session; preserve the reusable fix with validation so future identical use cases benefit.

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

### Rules

- Use `$HOME/.local/bin/bd` for ALL task tracking -- do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `$HOME/.local/bin/bd prime` for the memory search index, the newest memories, and the top of the ready queue. It does NOT carry a command reference or the session-close protocol -- those are in this file (`## Quick Reference`, `## Session Completion`), and bd's own non-memory output is a 367-byte header (measured). `bd prime` is bounded to ~4 KB by `scripts/beads-prime.sh` + `scripts/gen-beads-prime.py`, because the unbounded form is 4.6 MB and even a titles-only index was 157 KB -- past what the harness inlines, so it got persisted to a file and never read. The full title list is written beside it at `.beads/PRIME-memory-index.txt`; `scripts/test-beads-prime-size.py` keeps the output small.
- Use `$HOME/.local/bin/bd remember` for persistent knowledge -- do NOT use MEMORY.md files (and to READ a memory use `$HOME/.local/bin/bd recall <key>`, NOT `bd remember <key>` which clobbers it)

## Never End A Turn The User Would Answer With Nothing (user directive 2026-09-09)

Before ending a turn, ask what the ideal user reply is. If the honest answer is **nothing** -- they
would have to say "ok, go on" -- the turn must not end. Ending there costs the user a round trip
that carries zero information, and it is the single most common way this repo's work stalls.

The shapes that mean you stopped too early, all of them observed:

- Telling the user their input is not needed: "nothing", "the ball is in my court", "no action
  needed from you".
- Announcing your own next action instead of taking it: "Rebuilding and relaunching now", "next
  I'll ...", "let me now ...". If you know the command, run it.
- Offering work you are already authorised to do: "say the word and I'll ...", "want me to ...".
  The standing orders in this file already say yes; asking again is the round trip.
- Parking that work behind a condition instead of asking for it: "relaunching is the next step
  whenever you want it", "I can rebuild when you're ready", "just let me know", "on standby". It
  asks nothing, so it does not read as a question, and the only reply it admits is "yes, go on" --
  the answer to that sentence on 2026-09-15 was "I always want". Take the action; if something
  genuinely blocks it, say what blocks it in one line.
- A diagnosis with no edit. The finding is not the deliverable.

A turn MAY legitimately end on the user, and these are the only cases:

- A real external blocker: a credential, an interactive login, a purchase, a decision only they own.
- A question only they can answer: a preference, a subjective judgement.
- An in-game **observation** you have no oracle for ("did the popup appear?"), or an in-game
  **input** you have a reason not to drive yourself. The 2026-07-22 order that made asking for an
  input illegitimate was removed on 2026-09-23, so this is now a judgement call rather than a
  prohibition -- but a turn that ends here still has to say what it is asking for and why.
- The finished thing is delivered and you are reporting it.

This is enforced, not advisory: the Stop hook halts a turn whose closing message matches the first
list without matching the second. When it fires, do not rewrite the sentence to slip past it --
take the next action instead, then report what happened.

Asking for an in-game action or event means the receiver is already armed (user directive
2026-10-02: "you set no monitors, so you needed nothing from me"). Before a closing "I need you
to", "please <verb>", "let me know when" or "once you <verb>", the session must hold a live
`Monitor` on the instrument that captures the result, or a `run_in_background` wait whose own exit
is the event (an until-loop on the log). A backgrounded watcher or build does not count: its exit
is not what the user is being asked to cause. Questions answered by the user's typed reply --
judgement, preference, decision, credential, what they see where no oracle exists -- are exempt.
Enforced by `.cupcake/policies/claude/no_ask_without_receiver.rego` over
`.cupcake/signals/last_assistant_ask_without_receiver.sh`, tested by
`scripts/test-ask-without-receiver-signal.py` and `scripts/test-cupcake-stop-guards.py`.

A monitor or loop that is no longer needed is stopped first, before anything else (user directive
2026-10-07: "any time you have a monitor that runs after you don't need it, the first thing you
should do is stop it"). A turn may not end while a background Bash or Monitor task this session
started is still running, unless the closing message names that task id in a sentence saying why it
is still needed. Still running means no finished notification, no Monitor expiry and no TaskStop for
it in the transcript. Enforced by `.cupcake/policies/claude/no_unexplained_running_tasks.rego` over
`.cupcake/signals/last_assistant_unexplained_running_tasks.sh`, tested by
`scripts/test-unexplained-running-tasks-signal.py` and `scripts/test-cupcake-stop-guards.py`.

## RTK / Code Search Caveat

**Do NOT rely on `rtk` (the workspace RTK inspection wrapper) for code or identifier searches -- it produces false negatives and mangled output.** `rtk grep` REDACTS/aliases certain identifier tokens in BOTH its output AND its matching, so a search for a token that is actually present returns zero matches or garbled text. Confirmed redacted/aliased tokens include `online`, `continue`, `splash`, `experiments` (shown as `n`/`ln`), `input`, `block`, and `GOLD_SAVE` (shown as `n`) -- among others. Concretely, `rtk grep -n "fn apply_online_disable"` returns no matches even though the function exists, and `rtk grep "ONLINE_DISABLE_RVA"` exits 1 on a symbol that is present. `rtk find` / `rtk ls` are likewise flaky (empty output for valid queries). Treat any rtk-grep zero-result as untrustworthy, never as proof of absence.

**Prefer the harness `Read` tool and `python3 -c` regex one-liners for content/identifier searches** -- python reads the REAL file bytes and is unaffected by rtk redaction. Example:

```bash
python3 -c "import re,glob; [print(f'{f}:{i}:',l.rstrip()) for f in glob.glob('src/**/*.rs',recursive=True) for i,l in enumerate(open(f,encoding='utf-8',errors='replace'),1) if re.search(r'PATTERN',l)]"
```

Note the cupcake/OPA PreToolUse guard still INTERCEPTS raw `grep`/`ls`/`find`/`cat` bash commands and forces them through `rtk` (denying them otherwise), so you cannot just run bare `grep`. Use the `Read` tool and `python3` (neither is intercepted by the guard) instead of bash `grep`/`rtk grep` for inspection.

## Local Hidden Worktrees

- `/.worktrees/` is intentionally gitignored and may contain local git worktrees/sandboxes (for example `.worktrees/bevy-shader-tinkering`, a Bevy WGSL shader lab). Do not treat these directories as repo dirt, and do not delete/reconcile them unless the user explicitly asks.
- Work inside a `.worktrees/<name>` checkout only when that checkout is the intended active repo/branch. Do not merge sandbox contents into `main` just because they live under the repo root; persist shared policy in tracked root files instead.
- The Bevy shader lab is local tinkering by default. Productizing it into the main workspace requires an explicit user request and normal review of the `Cargo.toml`/`Cargo.lock` impact.

## Pre-Existing Issues Are Yours To Fix (user directive 2026-07-17)

Every pre-existing issue you encounter -- a red gate, a lint violation, a broken check, a tracked
file that already fails, latent tech debt -- is to be treated as **solely generated by you and
pre-authorized by the user**. Do NOT disclaim it ("not mine", "pre-existing", "out of scope",
"someone else committed this") and move on. You own it.

Handling rule:
- **Default: parallel background subagent.** If the issue does NOT interfere with your current task,
  dispatch a background subagent to fix it (make the gate green, remove the violation) while you keep
  working on your original objective. It is a real deliverable, not a footnote -- see it through to a
  clean state, left uncommitted for the main session to commit after the user's `authorize commit`
  (a subagent never commits or lints; see "Commits And Lints Wait For The User's `authorize commit`").
- **Blocker: do it first.** If the issue interferes with or blocks your current task (a gate you must
  pass to commit/validate, a broken dependency you need, a failure that makes your own proof
  untrustworthy), it is a blocker: fix it BEFORE the original task, not in parallel.
- **Blocker + major digression that touches this code: isolate it in a worktree.** When the fix is
  both a real blocker AND a substantial change to shared source (not a small localized fix), run the
  subagent in its OWN git worktree under an agreed untracked dir -- `.worktrees/<name>` (gitignored;
  see Local Hidden Worktrees) or under `./target` -- via the Agent tool's `isolation: "worktree"` or
  an explicit `git worktree add`. That keeps its edits/commits out of your main working tree so they
  cannot collide with your in-flight work; bring the result back only once it is green. A small,
  localized, non-conflicting fix does NOT need a worktree -- a plain background subagent in the main
  tree is fine (it must avoid the exact files you are editing).

The pre-authorization is standing, so you do not need to ask before fixing a pre-existing issue; just
fix it (in parallel, first, or worktree-isolated per the rules above) and report it alongside your
main work.

## Commits And Lints Wait For The User's `authorize commit` (user directive 2026-10-17)

The user's words: "Lets have a subagent policy to never run lints or formatting or perform
formatting ourselves until we are just about to commit, and only commit when authorized instead of
what your agent just did which is tank my FPS with a commit to a worktree when the files wouldn't
be lost anyway because we are in a virgin worktree in a persistent drive."

What happened: a subagent committed in a gitignored worktree on a persistent drive. The commit
preserved nothing the worktree was not already keeping, and its hooks ran the check suite while the
user was playing a game.

The rule:

- **A subagent never commits and never runs a lint or formatter.** Not in the main tree, not in a
  worktree, not with `git -C`, whatever the conversation says. It leaves its changes uncommitted
  where it made them and reports the paths; uncommitted files in a worktree on this drive are not
  at risk.
- **The main session commits only after the user's own reply `authorize commit`** -- that message
  and nothing else, typed by the user. One reply pays for one commit. Ask for it when the work is
  ready to keep; do not commit on a timer, after a runtime run, or to "preserve" anything.
- **Lints and formatters run only between that reply and the commit it pays for**: `cargo fmt`,
  `cargo clippy`, `rustfmt`, `shellcheck`, `ruff`, `black`, `prettier` and their kin,
  `scripts/check-comment-caps.py` (with or without `--fix`), `scripts/check-no-lossy-utf8.py`, and any
  `--fix` sweep. Never format files by hand to satisfy a formatter either. Tests are not lints and
  stay allowed at any time: `cargo test -p <crate>`, `opa test`, a gate's `--selftest`, and running
  a script to produce data. `scripts/check.sh` stays refused outright (`no_whole_check_sh`).
- **The commit is one plain command**: `git [-C <dir>] commit -F <file>` or
  `git commit -m '<single-quoted message>'`, with nothing chained to it -- no `git add -A &&`, no
  heredoc, no `$(...)`. Stage in a separate command first.

Enforced by `.cupcake/policies/claude/git_commit_authorization.rego` and
`.cupcake/policies/claude/no_lint_before_commit.rego` over the `commit_authorization` signal
(`scripts/cupcake_commit_authorization.py`, tested by `scripts/test-commit-authorization-signal.py`).
The signal reads the session transcript the same way the push signal does, shares its reader, and
records spent authorizations in the gitignored `.cupcake/state/commit-authorizations.jsonl`. When
either guard denies, it names the phrase to ask for.

This replaces the 2026-07-17 / 2026-07-21 "Commit Timing" rule, which told agents to commit after a
completed runtime run without asking. A completed run is now a reason to ask for `authorize commit`,
not a reason to commit.

For **`bd remember`**: record a memory **after** a step, and **only if the finding is worth keeping**
(validated, durable, non-obvious) -- not eagerly for every intermediate hypothesis or run. The bd
memory commit-immediately-after-runtime-validation-2026-07-17 describes the replaced commit rule and
no longer governs commits.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is complete when it is tested and reported, and committed only if the user replied `authorize commit`; it does not have to be pushed. Never push to `main`.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run tests and builds** (if code changed) - Lints and formatters wait for step 4
3. **Update issue status** - Close finished work, update in-progress items
4. **Commit only on the user's `authorize commit`, and leave the branch local unless the user
   authorizes the push.** A subagent never commits and never lints. The main session asks the user
   for `authorize commit` when the work is ready to keep; after that reply, and only then, it runs
   the lints and formatters the change touches, then makes the one plain commit the reply pays for
   (see "Commits And Lints Wait For The User's `authorize commit`"). Without that reply, leave the
   changes uncommitted and say which files they are. The agent does not
   push on its own initiative -- user directive 2026-09-17: *"I stopped the monitor, because quite
   frankly you should NEVER be pushing."* Not a tag, not a deletion, not main, not a force, not
   through a `Monitor`, and not because a CI-state hook reports a failing job. A CI report is
   context, never an instruction to update the remote.

   **The one exception (user directive 2026-10-07): an exact `authorize <sha>` reply.** The agent
   may push one branch if and only if the user's most recent typed message is exactly
   `authorize <sha>` -- nothing else in the message -- and `<sha>` (7 to 40 hex digits) is a
   prefix of that branch's current local tip. To get one, name the branch and give the user the
   phrase with the full tip sha: `authorize <git rev-parse refs/heads/<branch>>`. The push must
   then be the plain form `git push [-u] <remote> <branch>` (or `git -C <repo> push ...`) and
   nothing else in the command; run it in the background, because the pre-push hook runs the full
   check suite. One authorization pays for one push: a second push, a moved tip or a new commit
   needs a new `authorize <sha>` reply. Only the user's own typed prompt counts -- not assistant
   text, tool output, a system reminder, a task notification or an accepted prompt suggestion --
   and only the main session can use it, never a subagent. Without that reply, say plainly which
   commits are unpushed and on which branch, and hand the user the exact command.

   Enforced by `.cupcake/policies/claude/git_block_any_push.rego` over the `push_authorization`
   signal (`scripts/cupcake_push_authorization.py`, tested by
   `scripts/test-push-authorization-signal.py`), which reads the session transcript and the
   branch tip itself and records spent authorizations in the gitignored
   `.cupcake/state/push-authorizations.jsonl`. When the guard denies a push it names the branch
   and the exact phrase to ask for. `.cupcake/policies/claude/transcript_write_guard.rego` keeps
   the transcript the authorization is read from out of the agent's reach.

   **The phrase waits for a run of that exact tip.** Hand the user `authorize <sha>` only after a
   DLL built at that commit has run in the game and written its log, which is the evidence
   `scripts/check-runtime-evidence.sh` reads. A branch whose commits change `crates/` is refused
   at push time by the pre-push hook and by `ER-EFFECTS-REQUIRE-RUNTIME-EVIDENCE` until a log
   carries `build git=<tip>`, so an authorization asked for earlier is spent on a push that fails
   (user, 2026-10-16: "I knew it needed a run stamp to push. I was hoping you would figure it out
   (again) prior to asking me to authorize a push"; and 2026-10-19: "to authorize a push, you must
   have runtime evidence"). Before asking, run `bash scripts/check-runtime-evidence.sh <full tip
   sha>` from that checkout. If it exits 1, build the changed shells at the tip
   (`bash scripts/er-build-dlls.sh <package>`, in the background), launch them
   (`python3 scripts/er-run-branch.py --with <package>`), let the DLL write its log, and check
   again. A push that changes no crate needs no run. The Stop hook enforces this:
   `.cupcake/policies/claude/no_authorize_without_run_stamp.rego` over the
   `last_assistant_authorize_without_run_stamp` signal halts a closing message that asks for
   `authorize <sha>` while that check exits 1, and allows when it cannot measure.
5. **Clean up** - Clear stashes
6. **Verify** - Uncommitted changes named by path, any authorized commit made, and the unpushed
   branches named
7. **Hand off** - Provide context for next session / review path

**CRITICAL RULES:**
- Never commit without the user's exact `authorize commit` reply, one commit per reply; never run a
  lint or formatter outside that window; a subagent does neither
- Never push without the user's exact `authorize <sha>` reply for that branch's tip, one push per
  reply, and never ask a peer session or a subagent to push on your behalf
- Work is complete when it is tested and reported; committing waits for `authorize commit`, and
  publishing it is the user's call
- Naming an unpushed branch is not the banned "ready to push when you are" hedge -- the hedge
  offers work you could do, and this is work you may not do without their reply. State it as a
  fact, give the `authorize <full sha>` phrase for the branch, and move on
- Opening a pull request needs the branch on the remote, so an agent can open one only after an
  authorized push of that branch
<!-- END BEADS INTEGRATION -->

## Runtime-Affecting Refactor Feasibility

When the user asks whether a runtime-affecting refactor is possible/easy/safe, investigate first before answering. Do not guess from source shape alone. Minimum feasibility check: inspect the runtime entrypoints, loader/export expectations, staging scripts, existing probes, and the current known-working runtime proof path; identify what could break and what smoke would prove non-regression. Do not call the refactor non-breaking until a live runtime smoke passes. Never commit or push a runtime-affecting refactor to `main` before the required smoke proof exists.

## Upstream (`fromsoftware-rs`)

**Never file, open, or propose filing an upstream issue/PR/report** (against
`fromsoftware-rs` or any other external project) -- not even as a recommendation or
follow-up. When our code and upstream disagree (e.g. a struct offset mismatch), resolve
it **in this repo**: confirm the correct value via static RE of the binary, fix or pin our
side, and record the finding in `bd` for the next agent. Treat upstream as a read-only
reference we adopt from, never as a place we contribute back to.

## Conventions & Patterns

The full list is `docs/agents/conventions.md`. This entry stays here because
`scripts/color-spelling.baseline.json` counts its spelling in this file.

- **Write `color`, never `colour`** (user request 2026-10-20), in prose, identifiers, config keys
  and messages alike. `scripts/check-color-spelling.py` (in check.sh's `lint` stage) is a ratchet
  over every tracked text file: `scripts/color-spelling.baseline.json` records each file's count,
  and the gate fails when a file rises above its number, when a file with no entry has any, and
  when a file falls below its number without the baseline being lowered in the same diff. After
  respelling, run `python3 scripts/check-color-spelling.py --write-baseline` and commit the
  smaller numbers with the change. `_reasons` marks the files whose spelling is someone else's
  contract -- the build planner's JSON keys, the `colour_levels` key users already have in
  `er-cel-shade.toml`, vendored skills, the generated English word list -- so those need a
  migration rather than a respelling; a reason never lets its file grow. The Edit guard
  `.cupcake/policies/claude/edit_color_spelling_guard.rego` refuses an edit that adds one outside a
  double-quoted span; it does not judge `Write`, whose previous count it cannot see.
