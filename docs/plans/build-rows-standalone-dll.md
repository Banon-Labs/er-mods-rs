# Shipping "Load Build from URL" + "Generate Build Link" as a standalone DLL

Status: **abandoned, 2026-09-11.** The feasibility study that ran alongside the first execution
attempt came back **No**, and it falsifies this plan's premise. Phase 0 was executed and kept --
its measurement is below and in `scripts/audit-quit-menu-seam.py`. Phases 1-6 were never started:
no code was moved, no crate was armed, no conflict row was written.

Numbers elsewhere in this doc were measured on `feat/ersc-seam-owner-hunt` on 2026-09-10 and are
reproducible with the command beside them. They are still true; it is the conclusion drawn from
them that was wrong.

## The verdict in one line

**No.** The two rows cannot ship as a co-loaded standalone ME3 DLL, and a replacement shell is not
worth what it costs. Tracked in bd `er-effects-rs-ehvj`.

The original verdict above this line said the opposite, on the grounds that hook collision and
GFx-swap collision are "already solved and already gated". Both halves of that are wrong for *this*
feature specifically, and each of the four findings below is independently sufficient. The first
three came from the feasibility study and were re-verified from source during the halted execution
attempt; the fourth was found by Phase 0. Every one cites the file and line it was read at.

### 1. The hooks the two rows stand on cannot all enter the union

`system_quit_duplicate_add_cancel_button_hook`
(`crates/er-quickload/src/experiments/startup_hooks/quit_menu/system_quit_dialog_handlers.rs:1146`)
takes **five** arguments, `(dialog, label, action_fn, enabled_fn, keyguide_fn) -> usize`.
`er_hook::UnionFn` (`crates/er-hook/src/lib.rs:78`) is
`unsafe extern "system" fn(usize, usize, usize, usize) -> usize` -- four -- and the comment directly
above it already says "Not for float-arg or >4-stack-arg targets". In the Microsoft x64 ABI the
fifth argument lives at `[rsp+0x20]` in the caller's frame, which `union_dispatch` never forwards.

So the product installs it with a bare `MhHook::new`
(`experiments/startup_hooks/diagnostics/layout_global_hooks.rs:85`), on
`SYSTEM_QUIT_DUPLICATE_ADD_CANCEL_BUTTON_RVA = 0x920c90`
(`crates/er-title-flow/src/constants_autoload_state.rs:596`). A second DLL on that prologue is the
2026-08-23 silent-collision failure again. `scripts/check-shared-hook-rvas.py` would require a
`[[shared]]` row, and its `shared_mechanism_failures` check proves each named handler reaches a
union registrar and never reaches `MhHook::new` -- a proof a 5-argument detour cannot pass. The
gate would correctly refuse the declaration the mechanism cannot deliver.

The ratio across the whole quit-menu tree says the same thing structurally: counted on this branch,
`quit_menu/*.rs` holds 58 bare `MhHook::new` sites against 3 `register_union_hook` calls, and the
row-creating and row-routing hooks are all in the bare group.

And `0x920c90` is not the only contended address. Audited end to end, the two rows need **seven**
game addresses, **five of which the product also hooks** -- the cloner, both routing entry points
(`0x9610d0`, `0x9749f0`), the shared software-keyboard pair (`0x81d3d0`, `0x81d220`), the Scaleform
file-open prologue (`0x11ced80`, already a two-way `[[shared]]` with `er-armament-icons`) and
`MenuWindowJob::Run` (`0x7ad1c0`, documented as a deterministic sole owner after a second contender
broke it once). Two of those have a measured **silent** failure history, including the 2026-08-23
session where the product reported `file_open_observer_installed = true` with `file_open_hits = 0`
and every GFx swap it owns went inert without a crash or a log line. The full table, and the
correction of a wrong attribution in this doc's first draft, are in the appendix.

### 2. The Quit grid is one GFx derivation, and it fail-closes on a second owner

The patched tab is a single 2x3 grid: `er_gfx::options_02_040::QUIT6_GRID_CELL_NAMES`
(`crates/er-gfx/src/options_02_040.rs:88`) is one array of six cell names, and the two build rows are
`Item_2_0` / `Item_2_1` inside it. There is no per-row derivation to split: `quit6()` applies
`OPTIONS_02_040_QUIT6_EDITS` to the vanilla movie in one shot (`options_02_040.rs:174`).

Running it twice is designed to fail. `quit6` of an already-edited movie returns `Quit6Error::Edit`,
asserted by `crates/er-gfx/tests/options_02_040.rs:64`
(`quit6_of_already_edited_movie_fails_closed`). Whichever DLL derives second gets vanilla, so only
one image can own the tab.

The swap site is also nowhere near the move scope: both the `02_040` grid and the `02_990` link
field are swapped in
`crates/er-quickload/src/experiments/startup_hooks/loading_cover/profile_table_gfx_files.rs`
(`quit6` at line 815, `build_url_02_990::centered_build_url_editor` at line 750), and the link
field's on-screen position comes from `profile_05_010_editor_runtime.rs:1055` -- a file this plan
explicitly listed as *not* moving. A shell would have to take the Scaleform file-open interception
and the ProfileSelect editor runtime with it.

### 3. The row table is one table, and it gates an irreversible quit

`QUIT_ROW_TABLE_ROWS` (`crates/er-quit-menu-core/src/rows.rs:127`) is a single six-row array.
`system_quit_controller_is_a_quit_row` scans all six (`row_identity.rs:132`), and
`system_quit_row_gate_instant_quit` (`row_identity.rs:292`) is the single gate on the irreversible
`ExitProcess(0)` -- it returns true only on positive evidence that the activated row is
Return-to-Desktop. Two images each holding half a row table means each resolver reads an incomplete
table, so the gate loses the positive evidence it exists to require. That is a save-safety hazard,
not a cosmetic split.

### 4. The link field cannot leave the tree without two files the plan kept

Found while executing Phase 0, and independent of the three above. `build_url_editor.rs` calls ten
symbols defined in files the plan did not move: eight (`submit_build_url_keyboard`,
`build_url_menu_pump_tick`, `BuildUrlKeyboardOutcome`, ...) in `save_picker_path_editor.rs`, and
`place_text_input_02_990_caret_at_end` / `set_text_input_02_990_text` in
`profile_05_010_editor_runtime.rs` -- which the plan's own file table marks **"no -- ProfileSelect
browse surface"**. `system_quit_dialog_handlers.rs` has the same shape. The appendix lists all ten.

Coupling inside the `quit_menu/` tree, not the `use crate::*` glob, is what binds this slice. It is
also the blocker that would have hit first: Phase 2 moves `build_url_editor.rs`.

### Two live blockers that would have stopped Phases 1-2 anyway

Independent of the verdict, and both already tracked:

* bd `er-effects-rs-pq31` -- `er-quit-menu-core` has no `build.rs` (confirmed: the crate is
  `Cargo.toml` + `src/` and nothing else), while `save_picker_path_editor.rs:20` and
  `constants/autoload_state.rs:21` `include!` prologue byte tables generated into `OUT_DIR` by
  `crates/er-quickload/build.rs`. `save_picker_path_editor.rs` is the shared 02_990 editor path the
  build-url field rides on, so Phase 2 could not have moved it as written.
* bd `er-effects-rs-gx3s` -- a 7-DLL closure that includes `er_quit_menu` wedges before the title
  screen, reproduced on two branches, in both cases inside hook installation. `er_quit_menu` arms
  nothing today, so whatever that is, arming it is the wrong direction to push.

### What is still worth doing

The Phase 0 measurement, which is why it was kept. The `use crate::*` reach of the quit-menu files
is now counted and classified per symbol, reproducibly, by `scripts/audit-quit-menu-seam.py` plus
the appendix. It corrects the plan in the *opposite* direction to the verdict: the estimate was 32
new `QuitMenuHost` fields for the build-rows subset, and the real number is **zero**. Any future
shape of this extraction -- a replacement shell, a further `er-quit-menu-core` slice under bd
`er-effects-rs-pq31`, or nothing at all -- starts from that table rather than re-deriving it, and
starts knowing that the seam is cheap and the tree coupling is not.

## What is already done (do not redo these)

| Fact | Measured by |
|---|---|
| The import/export engine is already a standalone shell: `crates/er-build-import` (cdylib, own `DllMain`) over `er-build-import-runtime` + `er-build-import-core`, with `er-build-export` host-buildable | `cat crates/er-build-import/Cargo.toml` |
| A shell crate already exists and is already registered: `er-quit-menu` (112 lines) is in `me3_shells` in `scripts/check-rust-build.sh` (29 shells), so it builds, links and gets conflict-analysed every run | `grep -A32 me3_shells scripts/check-rust-build.sh` |
| A feature crate already exists: `er-quit-menu-core`, 16 files / 6,491 lines, with a 30-field `QuitMenuHost` function-pointer seam and game deps (`eldenring`, `er-hook`, `er-game-base`) already wired | `wc -l crates/er-quit-menu-core/src/*.rs` |
| Two-DLL hook collisions are gated by value, not by spelling: `check-shared-hook-rvas.py` reports 31 DLLs, 311 hook targets, 36 shared and all declared, and runs in `check.sh` with a `--selftest` | `python3 scripts/check-shared-hook-rvas.py` |
| The union mechanism is proven in production: `er-quickload` + `er-armament-icons` share `TITLE_SCALEFORM_FILE_OPEN_RVA` (0x11ced80) through `register_union_hook` / `register_shared_hook`, declared as `[[shared]]` | `scripts/me3-dll-conflicts.toml` |

## What is actually blocking it

`er-quit-menu-core` arms nothing. Its only public entry point is `install_host`, and it contains
**zero** `register_union_hook` / `register_shared_hook` calls -- so the `er-quit-menu` shell's
"arms nothing yet" comment is accurate, not stale. Its `Cargo.toml` *claims* the AddCancelButton
row cloning as product (B), but that machinery is still in `er-quickload`:

```
crates/er-quickload/src/experiments/startup_hooks/quit_menu/   17 files, 16,478 lines
```

and its `mod.rs` opens with `use crate::*`, i.e. the whole root-crate namespace. That glob is the
cost: every file that moves has to have its root-crate reach converted into `QuitMenuHost` function
pointers, the same way the existing 30 fields were.

### The files the two rows stand on

| File | Lines | Needed by the build rows? |
|---|---:|---|
| `build_url_editor.rs` | 700 | yes -- the whole native-keyboard flow |
| `build_url_row.rs` | 178 | yes |
| `generate_build_link_row.rs` | 8 | yes |
| `build_url_clipboard.rs` | 7 | yes |
| `system_quit_row_identity.rs` | 77 | yes -- positive row identity |
| `system_quit_dialog_handlers.rs` | 1,492 | yes -- row cloning + the label statics |
| `profile_rows_system_quit_menu.rs` | 2,132 | yes -- the row-population path (22 `MhHook::new` sites) |
| `save_picker_path_editor.rs` | 1,543 | partly -- the shared 02_990 editor path |
| `system_quit_hooks.rs` | ~? | yes -- the arm point (3 `MhHook::new` sites) |
| `profile_05_010_editor_runtime.rs` | 1,991 | no -- ProfileSelect browse surface |
| `save_swap_profile_table.rs` | ~? | no -- save-swap preview |
| `save_dest_commit.rs`, `save_picker_menu.rs`, `save_flow_boxes.rs` | -- | no -- Save Game flow |
| `system_quit_ownership_repro.rs`, `system_quit_repro_guards.rs` | -- | no -- diagnostics (26 `MhHook::new` sites) |

Plus `crates/er-gfx/src/build_url_02_990.rs` (468 lines), which is already in a shared crate and
does not need to move.

## The shape to build

Arm the **existing** `er-quit-menu` shell rather than create a new crate, and select the row set at
the arm call:

```rust
// crates/er-quit-menu-core/src/arm.rs  (new)
pub struct RowSet { pub load_character: bool, pub load_from_file: bool,
                    pub save_game: bool, pub build_url: bool, pub generate_link: bool }
pub const BUILD_ROWS_ONLY: RowSet = RowSet { build_url: true, generate_link: true, ..RowSet::NONE };
pub unsafe fn arm(rows: RowSet) -> Result<(), ArmError>;
```

Reasons this beats a fresh `er-build-rows` crate: the shell is already in `me3_shells`, already
conflict-analysed, already has the host seam and the panic reporter, and a fresh crate would need
the same row-cloning move anyway -- so a new crate adds registration work and subtracts nothing.
If the two rows must ship without the other three, that is a `RowSet`, not a second crate.

## Phases

**Status: Phase 0 done; Phases 1-6 abandoned, never started.** They are kept below as written so the
verdict above can be read against the work it cancelled, not as a plan anyone should now execute.
Nothing in `er-quickload` or `er-quit-menu-core` was moved, no `arm(RowSet)` was added, and
`scripts/me3-dll-conflicts.toml` was not touched.

Each phase was to end green on the **scoped gate list** below and leave the product DLL's behaviour
unchanged until Phase 5. Phases 1-3 were to be pure refactors of `er-quickload`, individually
revertible.

`bash scripts/check.sh` is deliberately NOT the per-phase gate. It fails closed inside an agent
worktree (`repo_root == */.claude/worktrees/agent-*`, exit 2) and refuses a second concurrent run
anywhere, so a worktree agent cannot run it and must not reach for `ER_CHECK_FORCE=1`. The
whole-workspace verdict is the orchestrator's, run once in the main tree at integration.

The per-phase gate, in full:

```bash
cargo test -p er-quit-menu-core                  # and -p er-gfx if that crate is touched
cargo fmt -p <each crate touched> -- --check
python3 scripts/check-comment-caps.py <each file touched>
python3 scripts/check-no-lossy-utf8.py
python3 scripts/check-shared-hook-rvas.py        # after ANY detour move
python3 scripts/check-me3-dll-conflicts.py       # after ANY conflict-table edit
cargo xwin build --release --target x86_64-pc-windows-msvc          # product, backgrounded
bash scripts/er-build-dlls.sh er-quit-menu                          # shell, records provenance
```

### Phase 0 -- pin the seam (counted 2026-09-10; refine, do not re-derive)

The symbols the moving files reach through `use crate::*` have been counted: definitions collected
from every `er-quickload/src/**/*.rs` outside the `quit_menu/` tree, intersected with the
identifiers each moving file uses (comments stripped).

| Move scope | Symbols reached | `const` (mechanical) | `fn` + `static` (become host fields) |
|---|---:|---:|---:|
| Build rows only -- the 7 files, without `profile_rows_system_quit_menu.rs` / `save_picker_path_editor.rs` | 54 | 22 | 32 |
| All nine files | 128 | 59 | 69 |

A build-rows-only extraction therefore adds roughly **32 fields** to the 30-field `QuitMenuHost`,
and the 22 consts move as data -- most already belong in `er-game-base::rva`
(`constants/anti_debug.rs` owns 27 of the full-scope symbols, `constants/stats_panel_text.rs` 22,
`constants/profile_render.rs` 20). The full-scope number more than doubles the behavioural seam,
which is the concrete argument for shipping `RowSet::BUILD_ROWS_ONLY` first and the other three
rows later.

What Phase 0 still owes: the per-symbol verdict on those 32 -- host field, move-with-the-code, or
delete -- because one function pointer per symbol is an upper bound, not a design.

**Done, and the estimate above is wrong by the whole amount: the answer is zero new fields, not 32.
Read the appendix, not this paragraph.** The upper bound was upper by more than anyone expected --
11 collisions, 23 symbols belonging to a file that should not have been in the slice, and 6 sitting
inside bodies that are already host fields.

Proof: the per-symbol table is this doc's appendix, and the worklist it classifies is regenerated
by `scripts/audit-quit-menu-seam.py`. Gates: `check-comment-caps.py` on the touched files,
`check-no-lossy-utf8.py`, `check-no-timeouts.py`, `check-shared-hook-rvas.py` (31 DLLs, 311 hook
targets, 36 shared and all declared), `check-me3-dll-conflicts.py`, and
`cargo test -p er-quit-menu-core` (43 passed). No build change, and `scripts/check.sh` is refused
inside an agent worktree by design, so it did not run and this is not a whole-workspace pass.

### Phase 1 -- move the row-cloning machinery into `er-quit-menu-core`

Move `system_quit_dialog_handlers.rs`, `system_quit_row_identity.rs` and the row-population half of
`profile_rows_system_quit_menu.rs` into `er-quit-menu-core`, converting each root-crate reach into a
host field from Phase 0's appendix. `er-quickload` keeps calling them; nothing is armed from the
shell yet.

Every detour that moves must become `er_hook::register_union_hook` -- the crate's own `Cargo.toml`
already mandates it, and `check-shared-hook-rvas.py` will fail the moment two shells claim one
address without a table row.

Proof: the scoped gate list above, with `check-shared-hook-rvas.py` mandatory because detours move
in this phase; the product DLL still loads and the three existing rows still work in the combined
runtime run.

### Phase 2 -- move the two build rows

Move `build_url_row.rs`, `build_url_editor.rs`, `build_url_clipboard.rs`,
`generate_build_link_row.rs` and the 02_990 editor path they share with the save picker
(`save_picker_path_editor.rs`, the shared half only). `er-build-import-runtime` is already a
dependency of `er-quit-menu-core`, so the engine side needs no change.

Proof: same as Phase 1, plus `cargo test -p er-quit-menu-core`.

### Phase 3 -- add the arm entry point

Add `arm(RowSet)` and route `er-quickload`'s existing arm site through it with the full row set, so
the product's behaviour is defined by the same code path the shell will use. This is the phase that
proves the seam is real: if the product can arm through it, a shell can.

Proof: the product DLL armed through `arm(RowSet::ALL)` is behaviourally identical in the runtime
run; `oracle_system_quit_*` counters match a pre-change run.

### Phase 4 -- arm the shell

Replace `er-quit-menu`'s "scaffolding: nothing armed yet" block with
`er_quit_menu_core::arm(BUILD_ROWS_ONLY)` behind its standalone host. The shell's host defaults must
stay neutral -- in particular the save-write bypass stays refused, which its current doc comment
already commits to.

Proof: the shell loaded **alone** in a me3 profile shows both rows on the Quit tab and imports a
build; the product DLL absent.

### Phase 5 -- declare the co-loading answer

Decide and record whether `er-quit-menu` and `er-quickload` are `[[conflict]]` or `[[shared]]` in
`scripts/me3-dll-conflicts.toml`. Both offer the same two rows, so the honest answer is almost
certainly `[[conflict]]` on a *duplicate-feature* kind rather than a hook kind -- the same relation
`er-build-import` already has with the product. Touch points:

| File | Change |
|---|---|
| `scripts/me3-dll-conflicts.toml` | the pair's row, with the reason |
| `scripts/check-me3-dll-conflicts.py` | nothing, if the row is added |
| `scripts/er-dll-closure.py` | nothing -- it reads `[[conflict]]` and will refuse the bad profile |
| `scripts/check-shared-hook-rvas.py` | nothing -- it discovers by value; it will simply start reporting the new pairs |
| `scripts/check-rust-build.sh` | nothing -- `er-quit-menu` is already in `me3_shells` |
| `scripts/er-dll-provenance.py` | nothing -- it attests whatever links |

Proof: `python3 scripts/check-me3-dll-conflicts.py`, `python3 scripts/check-shared-hook-rvas.py`,
and a generated profile that co-loads the pair is **refused**.

### Phase 6 -- the runtime proof matrix

Three runs, none of which may be replaced by a build success (per `AGENTS.md`: a rendered feature is
not proven by build success, launch success, or hook counters):

| Run | Profile | Must show |
|---|---|---|
| A | product alone | both rows present; import applies -- unchanged from today |
| B | `er-quit-menu` alone | both rows present; import applies; no product DLL loaded |
| C | product + `er-quit-menu` | the profile generator refuses to emit it |

Run B is the deliverable's proof. The oracle is the import's own telemetry plus the row-present
counter -- not a screenshot.

## Risks, in the order they will bite

1. **Feature unification.** `er-quit-menu-core` links `er-save-picker-core` with
   `default-features = false`. Cargo unifies features across a build graph, so the isolation is real
   only in a build where the product is absent. A shell-only build must be verified as its own
   cargo invocation, not as part of the 29-shell link.
2. **The `use crate::*` glob.** 32 behavioural symbols for the build-rows subset, 69 for the full
   quit menu. That gap is the price of taking the other three rows along, and it is why the plan
   ships `BUILD_ROWS_ONLY` first. -- *Superseded by the appendix: the real number is four new seam
   fields, and it was never the blocker.*
3. **The 02_990 swap.** The link field's derived movie is swapped at the Scaleform file-open
   prologue the product also hooks. That is exactly the pair that went silently inert for a day on
   2026-08-23 (113 hits alone, 0 co-loaded). Route through the union from the first line, never a
   bare `MhHook::new`.
4. **Row cloning arms twice.** If both DLLs ever load, two AddCancelButton detours clone two pairs
   of rows. Phase 5's refusal is the guard; do not rely on a runtime singleton to paper over a
   profile that should not exist.

## Open questions this plan does not answer

The feasibility study (bd `er-effects-rs-ehvj`) answered these, and the answers are the verdict at
the top of this doc:

- which of the five Quit-menu addresses the two rows actually need, vs. which belong to the other
  rows -- answered in the appendix below: the row cloner at `0x920c90` is the only one the build
  rows genuinely need, and it is the one that cannot enter the union;
- whether row cloning can be armed at all without the product's arm sequencing -- no, and the grid
  derivation makes the question moot;
- the file-by-file verdict on the 16,478 lines -- partially, in the appendix;
- the honest session count -- moot.

## Appendix (Phase 0) -- the per-symbol verdict

Regenerate the worklist with `python3 scripts/audit-quit-menu-seam.py build` (JSON) or
`--markdown`; `all9` gives the whole-quit-menu scope. The script collects every definition in
`crates/er-quickload/src/**/*.rs` outside the `quit_menu/` tree and intersects it with the
identifiers each moving file mentions, comments and string literals stripped. That intersection is
an **upper bound** -- a root-crate name can collide with a local binding or an inherent method --
so it is a worklist to classify by hand, which is what the rest of this appendix is.

### What the count actually is

| Scope | Raw intersection | Name collisions | Real reaches | New `QuitMenuHost` fields needed |
|---|---:|---:|---:|---:|
| Build rows -- the 7 files | 51 | 11 | 40 | **0** |
| All nine files | 112 | not classified | -- | -- |

The raw counts differ slightly from the 54/128 recorded in the Phase 0 table above because the
script's definition regexes were tightened. The shape of the answer does not change, and the
classification is what matters.

The plan estimated "roughly **32** fields" for the build-rows subset. The real number is **zero**.
One function pointer per symbol was always an upper bound; here it overcounts by the whole amount,
for four reasons the raw intersection cannot see.

**1. Eleven are name collisions, not reaches.** `HEAP_LO` and `NULL` are function-local `const`s
declared three times each inside `system_quit_dialog_handlers.rs` (`:9`/`:28`/`:126` and
`:10`/`:29`/`:127`); `EPOCH` is `pub(super) static EPOCH` at `system_quit_hooks.rs:184`, in the
file itself. `new`, `tick`, `drop`, `key`, `summary` are `AtomicUsize::new` / `MhHook::new`,
`er_build_import_runtime::tick`, `std::mem::drop`, a local `let key`, and `report.summary()`.
`CONFIG_FILE_NAME` and `game_directory_path` are already reached fully qualified through
`er_build_import_runtime::` and `er_game_base::log::`. `_` is the wildcard pattern.

**2. Twenty-three belong to `system_quit_hooks.rs`, which serves the wrong rows.** Read end to end
that file is the title Continue-confirm hook, the `EzChildStepBase::RequestFinish` teardown trace,
the load2 stuck-`TestNetStep` fix, and the three ProfileLoad hook installers. Nothing in it is
reached by a build row. Dropping it from the slice removes 23 of the 40 real reaches at no cost --
the plan should not have listed it.

**3. Six sit inside bodies that are already host fields.** `active_save_file_for_system_quit`,
`normalize_save_bytes_to_active_steam_id`, `save_picker_seamless_mode_after_settle`,
`system_quit_hash_bytes`, `system_quit_save_swap_lock` and the picker-dir pair are all reached from
inside `system_quit_ingest_picked_save` or `system_quit_env_save_path` -- and **both of those are
already `QuitMenuHost` fields**. Leaving their bodies in the product is not a compromise; moving
them would convert one existing field into six new ones.

**4. What is left is data or a shared-crate move, never a function pointer.**

### The symbols that genuinely needed a decision

| symbol | kind | verdict | note |
|---|---|---|---|
| `append_autoload_debug` | fn | existing field | 108 sites across the 7 files; declared and wired |
| `release_input_block_now` | fn | existing field | the Return-to-Desktop arm of both routing hooks; declared, wiring is the work |
| `append_crash_log` | fn | existing field | one site, in the Scaleform dtor guard, out of scope anyway |
| `save_picker_seamless_mode_after_settle` | fn | existing field | declared; reached from inside the ingest |
| `normalize_save_bytes_to_active_steam_id` | fn | existing field | declared as a reshaped `fn(&mut [u8]) -> bool` |
| `remember_preferred_save_picker_dir` | fn | existing field, other seam | `er_save_picker_core::SavePickerHost::remember_picker_dir` is already wired, and its doc comment names this exact function |
| `autoupdate_preferred_picker_dir_enabled` | fn | delete | only the `if` guard on the line above; fold it into the remember call, as `path_hooks.rs:1457` already does |
| `system_quit_hash_bytes` | fn | delete | the definition is one line: `er_game_base::fnv1a::fnv1a64(bytes)`. Call that. |
| `SYSTEM_QUIT_PROFILESELECT_NATIVE_CLOSE_RVA` | const | delete | `constants/profile_render.rs:77` defines it as `er_game_base::rva::MENU_WINDOW_CLOSE_WITH_FAILED_RVA as u32` -- a second name for an address `er-game-base` already publishes |
| `callstack_contains_game_rva` | fn | move to `er-game-base` | load-bearing for the build rows: it identifies the two native Quit-row return addresses inside the row cloner (`:1172`, `:1176`). Pure `RtlCaptureStackBackTrace` + `GetModuleHandleA`, zero product state |
| `trace_first_game_caller_rva` | fn | move to `er-game-base` | same two calls, adjacent in the same file; moving both retires a redundant field `er-loading-portrait-core` carries today |
| `seamless_coop_loaded` | fn | move to `er-game-base` | `:1291` picks the help flavour inside the same `cloned_rows` table that carries both build rows. `GetModuleHandleA("ersc.dll")` plus a sticky latch -- one latch for every DLL |
| `leak_installed_hook` | fn | move to `er-hook` | a no-op `fn(MhHook)` sitting beside `pub use er_hook::*`; moving it keeps all 56 `crate::mh::leak_installed_hook` sites compiling unchanged |
| `ExitProcess` | fn | move with the code | an `ffi.rs` extern declaration; `er-save-picker-core` already calls `windows::Win32::System::Threading::ExitProcess` directly, and `rows.rs` already owns the gate on it |
| the 14 `SYSTEM_QUIT_PROFILE_LOAD_*` constants and cells | const/static | one data move, not 14 fields | see the group note below |
| the 4 `MOVEMAPSTEP_*` offsets | const | move the caller out instead | see the group note below |
| `SYSTEM_QUIT_CONTINUE_CONFIRM_ORIG`, `SYSTEM_QUIT_WINDOW_LIST_PUSH_ORIG` | static | data, with the group | Load Character and the ProfileSelect list observer; neither is a build row |
| `install_auto_accept_hook` | fn | stays | reached only from `system_quit_save_game_start_flow`, the Save Game row |
| `HEAP_LO`, `NULL`, `EPOCH`, `new`, `tick`, `drop`, `key`, `summary`, `CONFIG_FILE_NAME`, `game_directory_path`, `_` | -- | collision | see above |

### Group collapses -- the design point

* **The ProfileLoad hook trio is 14 symbols and zero seam entries.**
  `SYSTEM_QUIT_PROFILE_LOAD_{ACTIVATE,CONFIRMED,JOB_RUN}_{RVA,ORIG,NOT_INSTALLED,INSTALLED_YES}`
  plus two `_LAST_*` captures are one concept: three MinHook installs on the Load Character route.
  Their `*_INSTALLED` siblings already live in `er_telemetry_core::counters`, which both crates
  depend on and which already holds 37 `*_ORIG` `AtomicUsize` cells. So this is one move when the
  Load Character slice goes: `*_ORIG` and the state constants to `er_telemetry_core::counters`,
  `*_RVA` to `er_quit_menu_core::quit_dialog_layout`, which already owns `PROPERTY_EDIT_DIALOG_*`
  and `DIALOG_GRID_CONTROL_A38_OFFSET`. A `pub const` and a `pub static AtomicUsize` read fine from
  both sides -- hook plumbing never needs a function pointer.
* **The four `MOVEMAPSTEP_*` offsets are one misfiled function.** All four are read by
  `maybe_force_finish_stuck_testnet_step`, the load2 world-completion fix, which is driven from
  `experiments/lifecycle/task_tick.rs` and touches no menu, no dialog and no row. The fix is to move
  that function out of `quit_menu/`, not to seam its offsets.
* **The two stack walkers are one concept**, adjacent in `crashlog/module_resolution.rs`, with no
  product state at all. They belong beside `game_module_base()` in `er-game-base`.

### The finding that outranks the count

The audit measures root-crate reaches. It is blind to coupling **inside** the `quit_menu/` tree, and
that is where the slice actually binds. `build_url_editor.rs` -- the most build-rows-specific file
in the list -- calls ten symbols defined in files the plan did **not** move:

| symbol | defined in | plan's verdict on that file |
|---|---|---|
| `submit_build_url_keyboard`, `take_build_url_keyboard_outcome`, `build_url_keyboard_active`, `build_url_menu_pump_tick`, `build_url_keyboard_latch_is_abandoned`, `release_abandoned_build_url_keyboard`, `reset_build_url_keyboard_state`, `BuildUrlKeyboardOutcome` | `save_picker_path_editor.rs` | "partly -- the shared 02_990 editor path" |
| `place_text_input_02_990_caret_at_end`, `set_text_input_02_990_text` | `profile_05_010_editor_runtime.rs` | **"no -- ProfileSelect browse surface"** |

So the link field cannot move without `profile_05_010_editor_runtime.rs`, which the plan excluded
outright, and without `save_picker_path_editor.rs`, which `include!`s an `OUT_DIR` prologue table
from `crates/er-quickload/build.rs` that `er-quit-menu-core` has no `build.rs` to generate (bd
`er-effects-rs-pq31`). `system_quit_dialog_handlers.rs` has the same shape.

**Intra-tree coupling, not the `use crate::*` glob, is the binding constraint on this slice.** That
is a fourth blocker, independent of the three at the top of this doc, and it is the one that would
have stopped Phase 2 first.

### Hook addresses, by which row needs them

Audited end to end, not just the five the shell's `Cargo.toml` names. The `quit_menu/` tree installs
detours on **38 distinct game addresses**, plus three more installed elsewhere whose behaviour it
owns. No raw byte-patch of the game image appears anywhere in the directory.

Two framing facts first, because they change what the rest means:

* **`er-quit-menu` and `er-quit-menu-core` install zero detours today.** Both crates contain no
  `MhHook::new`, no `register_union_hook`, no `register_shared_hook` -- the single textual match in
  `er-quit-menu/src/lib.rs:10` is a doc comment. `check-shared-hook-rvas.py` attributes 0 hook
  targets to `er-quit-menu` against 191 to `er-quickload`. The shell's `Cargo.toml` paragraph about
  contending on these addresses is an unimplemented **design contract**, not a description of
  shipped code -- which is why the gate's "all declared" says nothing about this feature yet.
* **That gate keys declarations on the crate pair, not the address** (`check-shared-hook-rvas.py:750`
  tests `frozenset({a, b}) not in declared`). One `[[conflict]]` row between two crates therefore
  satisfies it for *every* address those two share. Only `0x11ced80` and the DirectInput slot are
  declared per-address today. "36 shared and all declared" is a weaker statement than it reads as.

#### What the two build rows actually need -- seven addresses, five of them contended

| RVA | what | installed by | collides with the product? |
|---|---|---|---|
| `0x920c90` | `AddCancelButton` -- the row cloner; `QuitRow::LoadBuildFromUrl` and `GenerateBuildLink` are entries 3 and 4 of its one `cloned_rows` table | `diagnostics/layout_global_hooks.rs:85`, bare `MhHook::new`, 5 args | **yes** -- the product still needs it for the other three rows, and two DLLs would each append rows and each call `SetItemCount` |
| `0x9610d0` | `SYSTEM_QUIT_RETURN_DESKTOP_ACTION_DO_CALL_RVA` -- the cloned rows inherit the *second* `AddCancelButton` call's `action_fn`, so a press enters this thunk | `system_quit_ownership_repro.rs:1093` | **yes** -- the product's router owns the other rows' arms of the same `match` |
| `0x9749f0` | `PropertyNewButtonController::Activate` -- the second routing entry; the dispatch collapses cloned buttons onto the native Return-to-Desktop controller | `system_quit_ownership_repro.rs:1138` | **yes**, same reason |
| `0x81d3d0` | software-keyboard per-frame result gate -- tells accept from back-out | `save_picker_path_editor.rs:533` | **yes** -- one installer serves both the link field and the picker path editor, disambiguated only by `KeyboardPurpose` |
| `0x81d220` | software-keyboard terminal callback -- captures the typed link | `save_picker_path_editor.rs:550` | **yes**, same installer |
| `0x11ced80` | `TITLE_SCALEFORM_FILE_OPEN_RVA` -- the interception the `02_990` link-field movie rides | `loading_cover/title_resources_stats_text.rs:110`, `register_union_hook` | **already a two-way `[[shared]]`** with `er-armament-icons`; a third registrant makes it three |
| `0x7ad1c0` | `MenuWindowJob::Run` -- the only context the field's per-frame caret/clipboard/pump work runs in | `er-title-flow/src/product_autoload_gates.rs:1129` | **yes** -- documented at `layout_global_hooks.rs:40-47` as the deterministic sole owner, precisely because a second contender broke this once |

Plus a non-hook dependency that fails silently: `SYSTEM_QUIT_QUIT_TAB_BUILDER_RVA = 0x958910` and its
`+0x110` / `+0x227` return addresses, read through `er_title_flow::system_quit_row_return_rvas()`.
The cloner stack-matches these to know which `AddCancelButton` call it is in; if they do not
resolve, `system_quit_dialog_handlers.rs:1167` forwards and **no rows are cloned at all**.

#### The five the shell's `Cargo.toml` names

Only one of them is a build-row address, and one line of the earlier draft of this appendix got a
second one wrong -- corrected here.

| RVA | what | installed by | needed by the build rows? |
|---|---|---|---|
| `0x920c90` | `AddCancelButton` row cloner | `layout_global_hooks.rs:85` | **yes** -- the only one |
| `0x9a4670` | ProfileLoad activate | `system_quit_hooks.rs:667`, union | no -- Load Character; fires on slot activation inside `05_010_ProfileSelect` |
| `0x875590` | ProfileSelect list builder | `save_picker_menu.rs:3078` | no -- Load Character from File; re-stages browse rows |
| `0x7ac720` | `MENU_WINDOW_JOB_DTOR_RVA` -- return-to-title crash guard, nulls a doomed `owningMenuWindow` at `job+0x130` | `system_quit_ownership_repro.rs:824` | **no.** An earlier draft of this table called this "row routing, shared with the other rows" and attributed it to `system_quit_dialog_handlers.rs`. Both halves were wrong; it is a crash guard for a return-to-title AV the build rows never reach |
| `0xd3ed90` | `MenuOffscrRendParam` lookup -- `ExitProcess(0)` when the table is absent during quit teardown | `system_quit_ownership_repro.rs:980` | no -- Return-to-Desktop, and now only a fallback |

#### Why this makes finding 1 worse, not better

The plan assumed one contended address that a union could absorb. It is **five**, the union cannot
take the first one at all (finding 1), and two of the five have a measured silent-failure history:

* `0x11ced80` -- on 2026-08-23, in an 11-native profile, the product reported
  `file_open_observer_installed = true` with `file_open_hits = 0` for an entire session, and every
  GFx swap it owns went silently inert, both `02_990` text-input movies included.
* `0x7ad1c0` -- a second contender on this prologue already broke things once, which is why
  `layout_global_hooks.rs:40-47` records the product's PAB detour as the deterministic sole owner.

Silent is the operative word: neither failure crashed, and neither wrote a log line saying the
feature was gone.

#### `build_url_editor.rs` installs nothing of its own

Confirmed: zero `MhHook::new`, zero union registration, zero `mh_install_hook_once` in that file. It
rides three surfaces it does not own -- the shared keyboard pair above, `MenuWindowJob::Run`, and the
Scaleform swap, which is not a file on disk but an in-place `MemoryFile` payload swap. The link field
and the picker path editor stay apart by cache key (`02_990_TextInput_BuildUrl` against
`02_990_TextInput_PathEditor`), with the observer rewriting `open_url` to the canonical
`data0:/menu/win/02_990_textinput.gfx` so the game's shared cache entry is never touched.

### Why none of this rescues the plan

The seam was never the blocker. The three findings at the top are about the hook ABI, the GFx
derivation and the row table, and none of them gets cheaper when the seam turns out to cost zero new
fields. The measurement is kept because the next `er-quit-menu-core` slice (bd `er-effects-rs-pq31`)
starts from it instead of re-deriving it.
