# The nine files that exist only in the er-quit-rows fork

Prerequisite sweep for phase 3 of the menus-and-saves consolidation (bd `er-effects-rs-8rho`).
The deletion itself is blocked on phase 2 (bd `er-effects-rs-w0ev`); this document is the
per-file adjudication that issue asks for, done now so the deletion commit can carry verdicts
rather than derive them under pressure.

Measured 2026-09-19 against this worktree. `crates/er-quickload/src` and
`crates/er-quit-rows/src` hold 132 files each; nine exist only in the fork and nine only in the
product. That symmetry is the first finding, and it reframes most of the sweep: **five of the
nine fork-only files are the product's own files at an older path.** `er-quickload` reorganised
`experiments/startup_hooks/` on 2026-09-12 (`7ef41b9e`, `cbdaf4a2`) and the fork, created a day
earlier at `28ca8f89`, never received it. The picker and diagnostics modules moved out of
`quit_menu/` into `save_picker/` and `diagnostics/`, two more were deleted outright in favour of
glob re-exports from `er-quit-menu-core`, and the fork still carries the pre-move layout.

## Method

Each file was adjudicated on four questions, all answered from file bytes rather than from `rtk`:

* who references it, inside the fork and outside it;
* whether an equivalent exists on the product side, and if so how the two differ;
* whether its module is reachable from the fork's `lib.rs` in the **default** feature set
  (`default = ["quit-rows", "save-picker", "menu-trace"]`, so `autoload`, `build-rows`,
  `loading-cover` and `portrait` are all off);
* whether anything outside `crates/er-quit-rows/` names any of its symbols.

Top-level item names were extracted from both sides of each pair and differenced. That comparison
is the load-bearing evidence below, so its shape matters: a counterpart containing every symbol of
the fork's copy and more is a superset, and the fork's copy is then a strictly older version of a
file that still exists.

## Reachability, and the kind of dead that does not apply

The issue warns that a file behind a non-default feature is a different kind of dead from one
nothing references. **That distinction does not apply to any of the nine.** Every one of them is
reachable in the fork's default build:

* `experiments/mod.rs:56` declares `mod boot_view_clock;` with no `cfg`;
* `experiments/lifecycle.rs:8` declares `mod save_flow;` with no `cfg`;
* `experiments/startup_hooks/quit_menu/mod.rs` declares the six `quit_menu/` modules at lines 24,
  55, 58, 61, 64 and 70, none of them gated. The `build-rows` gates in that same file sit on lines
  32 to 49 and cover a different set of modules (`build_url_row`, `generate_build_link_row`,
  `build_url_editor`, `build_url_backdrop`), all four of which exist on both sides and are
  therefore not part of this sweep;
* `lib.rs:6` declares `pub mod menu_window_run_install;` above the `#[cfg(windows)]` block, so it
  compiles and its tests run on the host.

So all nine are live code in the shipped fork DLL. Whatever is dead about them is dead because
something else already does the job, not because a flag turns them off.

## Verdicts

| file (under `crates/er-quit-rows/src/`) | lines | verdict | why |
|---|---|---|---|
| `experiments/boot_view_clock.rs` | 34 | Port into `er-quickload` | The only file with no counterpart anywhere. The product has the same clock inlined in `gpu_readback/boot_progress.rs`; the extraction is real work that landed only here |
| `experiments/lifecycle/save_flow.rs` | 1,535 | Dead with the fork | Superseded by `er-quit-menu-core/src/save_flow.rs` (1,787 lines, 40 top-level items against the fork's 32, zero fork-only). `er-quickload` deleted its copy at `7ef41b9e` and glob re-exports the core's |
| `experiments/startup_hooks/quit_menu/profile_05_010_editor_runtime.rs` | 1,394 | Dead with the fork | The same file at an older path. Product copy is `save_picker/profile_05_010_editor_runtime.rs`, 1,404 lines, identical 48-item symbol set; the 10-line delta is five `#[cfg(feature = "quit-rows")]` attributes the fork drops |
| `experiments/startup_hooks/quit_menu/save_dest_commit.rs` | 75 | Dead with the fork | A five-function arity adapter over `er_quit_menu_core::save_dest_commit_runtime`. All five wrappers and the `SAVE_JOB_OBSERVER` const now live in `er-quit-menu-core/src/save_flow.rs:157-198` |
| `experiments/startup_hooks/quit_menu/save_flow_boxes.rs` | 712 | Dead with the fork | Superseded by `er-quit-menu-core/src/save_flow_boxes.rs` (1,082 lines, 55 items against 25, zero fork-only). Bodies match line for line where both have them |
| `experiments/startup_hooks/quit_menu/save_picker_menu.rs` | 75 | Dead with the fork | The same hook-installer shim at an older path. Product copy is `save_picker/save_picker_menu.rs`, 81 lines, identical 6-item symbol set; the delta is one `#[cfg]` pair |
| `experiments/startup_hooks/quit_menu/save_picker_path_editor.rs` | 9 | Dead with the fork | A single `pub(crate) use er_quit_menu_core::software_keyboard::*;` under a doc comment. Product copy is `save_picker/save_picker_path_editor.rs`, 10 lines, the extra line being the `#[cfg]` |
| `experiments/startup_hooks/quit_menu/system_quit_ownership_repro.rs` | 1,275 | Dead with the fork | The same file at an older path. Product copy is `diagnostics/system_quit_ownership_repro.rs`, 1,309 lines, 32 items against 31, zero fork-only |
| `menu_window_run_install.rs` | 116 | Dead with the fork | Its predicate is superseded by `er-quickload/src/menu_window_run_gate.rs`, and the detour it gates is one the merged shell does not install |

Five thousand two hundred and twenty-five lines, of which thirty-four move.

Nothing outside `crates/er-quit-rows/` names a symbol from any of the nine. There are 137
references to the fork elsewhere in the tree, and every one of them addresses the crate as a whole:
build lists (`scripts/check-rust-build.sh:140`, `scripts/check.sh:2611`), the conflict table
(`scripts/me3-dll-conflicts.toml`, four pairs), gate baselines
(`scripts/audit-1170-gate-bypass.baseline.json`, 26 entries), log filenames
(`er-quit-rows-debug.log`, in five scripts), and the workspace member list. None of that reaches
into the nine.

## boot_view_clock.rs, the one that moves

Thirty-four lines: a `OnceLock<Instant>` named `BOOT_VIEW_EPOCH` and two readers,
`boot_view_epoch_ms` (anchors on first call) and `boot_view_epoch_ms_if_anchored` (reads without
anchoring). Pure `std`, no game in it.

The product has exactly this code, inlined in `experiments/gpu_readback/boot_progress.rs` at lines
161, 1323 and 1336 -- a 2,800-line module that also owns the loading cover's bar, its epoch
sequence and its composite cap. The fork's module doc states the reason for pulling it out, and the
reason is a real defect in the product's arrangement:

> the clock outlived the thing it was named after -- the System>Quit switch guards, the input
> block, the own-stepper and the native loading-screen exposure all stamp against it, and none of
> them draws a cover. Leaving the clock inside the cover module made `loading-cover` a feature
> seven unrelated callers depended on.

That coupling is currently latent in `er-quickload` rather than active: the crate declares
`loading-cover` and gates nothing on it (0 `cfg` sites, see below), so the clock is never actually
cut off there. It is active in the fork, which carries 17 `loading-cover` gates -- which is why the
extraction happened here and not in the product. Anything that later compiles the cover out of
`er-quickload` will need this module, and the merged shell from phase 2 is that build by
construction.

The destination named in the verdict is `er-quickload`, because that is where the duplicate lives
and the move is a lift-out of `boot_progress.rs`, not a new dependency. It is worth saying plainly
that `er-quit-menu-core` would be the wrong home -- the clock has no menu semantics -- and that
`er-telemetry-core` is arguably the right one: it already owns `BOOT_VIEW_EPOCH_SEQ` and
`BOOT_VIEW_EPOCH_KIND`, and its `counters/loading_cover.rs:349` already carries a comment
describing this clock's origin from the outside. Six consumers reach the clock through injected
function pointers today (`er-loading-portrait-core/src/host.rs:71`,
`loading_cover_host.rs:52`), so either destination works without changing a seam. The cheap version
is the lift-out; the correct version is the crate move. Neither is expensive, and the choice can be
made in the deletion commit.

## menu_window_run_install.rs, the one that looks like a port and is not

This is the only fork-only file written after the fork, at `6d82e9e1` on 2026-09-11, and it fixes a
measured defect: dropping `autoload` from the shell's default features turned off
`system_quit_menu_window_run_post` with no build failure and no log line, so a submitted
`05_010_ProfileSelect` drew behind a pause menu nothing hid and the Save Game row gave up 180 ticks
later. The predicate splits the install question by consumer, `boot_autoload || quit_rows`.

`er-quickload/src/menu_window_run_gate.rs` carries the identical predicate under the name
`menu_window_run_detour_required`, written two days later against a second measured run
(`br-20260913-154820-c63f`) and naming four consumers rather than two. Four of the fork's five
tests exist there verbatim. The product is the later and fuller statement of the same decision.

The fork's one unique symbol is `quit_rows_armed()`, a `pub const fn` returning `true`, read at
three call sites (`experiments/lifecycle/task_tick.rs:316`,
`experiments/gating/env_flags.rs:307`, `profile_select_chrome_gate.rs:112`). The product answers
the same term with `cfg!(feature = "quit-rows")`. Neither spelling survives phase 2, whose whole
point is a row set selected from a config file beside the game exe rather than from a cargo
feature -- so the term becomes "did the selected `RowSet` include a cloned row", which is a
question for that design and not a line of code to carry across.

More decisive than any of that: the detour this predicate gates is `PAB_NODE_UPDATE_RVA`
(`0x7ad1c0`), and the merged shell does not install it. `er-quit-menu-core/src/arm.rs:172` arms
`menu_pump::install_quit_menu_window_run_hook()` instead, which is the core's own owner of the same
address. The product's gate doc says why the two must not both exist, at
`menu_window_run_gate.rs:51-55`: a second owner on that address loses the `MH_CreateHook` race
rather than chaining with it, which is the race that removed
`install_system_quit_menu_window_job_run_hook` on 2026-07-15. A shell built over the core has its
pump already and must not ask this question at all.

## What else goes when the fork goes

Two things worth naming in the deletion commit, neither of them a blocker.

`scripts/check.sh:2611` runs `cargo test -p er-quit-rows -p er-input-harness`, and the comment
above it counts 19 host lib tests in the fork plus two integration tests. Five of the 19 are in
`menu_window_run_install.rs`; four of those five have verbatim twins in
`er-quickload/src/menu_window_run_gate.rs`, and the fifth
(`this_shell_needs_the_pump_whatever_the_boot_autoload_does`) is the one that reads
`quit_rows_armed()`, so it goes with the symbol. The integration test
`tests/no_message_box_is_answered.rs` exists on both sides and is not lost. The `-p er-quit-rows`
half of that check.sh line has to go with the crate.

`scripts/check-constant-feature-gates.py:27` names `quit_rows_armed` in its module docstring as the
worked example of why the `_armed` suffix came out of its rule. Deleting the fork leaves that
sentence naming nothing. It is prose, not an assertion, so the gate does not fail -- but the
example should be replaced or the paragraph rewritten, in the same commit, or it becomes the kind
of stale note that costs a session.

## The two cleanup claims

**`save-picker = []` is declared on both crates and gates zero `cfg` sites in either.** Holds, and
is wider than stated. `er-quickload/Cargo.toml:57` and `er-quit-rows/Cargo.toml:34` both declare
it; both name it in their `default` set (`er-quickload` line 31, `er-quit-rows` line 21); and
neither crate contains a single `cfg(feature = "save-picker")`. The feature is also named in
`scripts/gate-quickload-quit-rows-deadcode.py:32`, which passes it on a `--features` line, so
removing the declaration is a two-file edit rather than a one-file one.

The same sweep found three more features in the same condition, all in `er-quickload`:
`build-rows`, `loading-cover` and `portrait` are declared and gate zero `cfg` sites there.
`loading-cover` and `build-rows` are live in the fork (17 and 17 sites); `portrait` is dead in
both. Feature `cfg` counts, measured across each crate's whole `src` tree:

| feature | `er-quickload` | `er-quit-rows` |
|---|---|---|
| `quit-rows` | 119 | 1 |
| `autoload` | 31 | 2 |
| `build-rows` | 0 (not declared) | 17 |
| `loading-cover` | 0 | 17 |
| `menu-trace` | 2 | 2 |
| `save-picker` | 0 | 0 |
| `portrait` | 0 | 0 |

Deleting the fork removes the only consumer of `build-rows` and `loading-cover` as gating features,
which turns two more declarations into the same kind of empty flag `save-picker` already is. That
belongs in the deletion commit's scope, not in a follow-up.

**`er-quit-rows` names `features = ["boot-flow", "os-dialog"]` on its `er-save-picker-core`
dependency unconditionally, where `boot-flow` is unreachable and `os-dialog` is load-bearing.**
The declaration holds exactly as written (`Cargo.toml:76`), but it is not distinctive: `er-quickload`
names the identical pair at its own line 102, and `er-save-picker-core`'s own `default` is
`["boot-flow", "os-dialog"]`. The `os-dialog` half is confirmed load-bearing --
`experiments/startup_hooks/save_picker/mod.rs:5` does
`use er_save_picker_core::os_dialog::{no_picker_cover, os_pick_validated};`, and
`er-save-picker-core/src/lib.rs:90` puts that module behind the feature, so removing it fails to
compile.

The `boot-flow` half needs splitting in two, because the claim is true at runtime and false at
compile time, and the difference decides whether the feature can simply be dropped.

At runtime it is unreachable, for the reason the code states at
`experiments/gpu_readback/save_picker_overlay.rs:30`: `boot_arm_missing_save_picker_in_game()`
returns `false` outright when `cfg!(feature = "autoload")` is off, which it is in this shell's
default set. The comment records why that guard was added -- with no autoload there is nothing for
the boot picker to answer, and arming it holds `TitleTopDialog::open_menu` forever, measured
2026-09-11 as a title sitting through twelve confirms. So nothing in the shipped fork ever arms the
boot picker.

At compile time it is required. `er_save_picker_core::overlay` is
`#[cfg(feature = "boot-flow")]` (`lib.rs:92`), and the fork's `save_picker_overlay.rs:5-12`
re-exports nine statics and six functions from it with no gate of its own, several of which are
read on the live path (`save_picker_overlay_active` from `lifecycle/task_tick.rs:102`,
`save_picker_overlay_process_completion` from `dll_entry_parts/task_registration.rs:299`). Dropping
`boot-flow` from the dependency line fails the build. Cargo feature unification makes the
declaration doubly hard to reason about from the manifest alone: `er-quit-menu-core` deliberately
turns `boot-flow` off, and `er-save-picker-core/src/lib.rs:73-79` records that the feature
therefore cannot carry the requirement at all -- only the explicit arm entry point can, which is
precisely what the `autoload` guard above is.

So the accurate version of the claim: `os-dialog` is load-bearing and stays; `boot-flow` is armed
by nothing in this shell but is still a link-time requirement of the overlay symbols it re-exports,
and separating the always-compiled overlay readers from the boot arm is what would let it go. That
separation is not part of deleting the fork.

## Ambiguity worth recording

One verdict rests on a judgement rather than on a symbol difference. `boot_view_clock.rs` is called
a port because the extraction is genuinely absent from the product, but its 34 lines are duplicated
code, not unique behaviour -- an agent that decided to delete it and leave `boot_progress.rs`
holding the clock would lose nothing that runs today. The port is worth doing for the shape it
gives the merged shell, and the destination crate is an open choice between `er-quickload` and
`er-telemetry-core`. That choice should be made deliberately in the deletion commit rather than
inherited from this document.

Everything else is settled by the symbol differences: six counterparts contain every top-level item
of the fork's copy, and `save_dest_commit.rs`'s single fork-only item is a private const with a
twin at `er-quit-menu-core/src/save_flow.rs:157`.
