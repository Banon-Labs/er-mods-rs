# Menus and saves: consolidation plan

**Baseline:** `3399d75b` on `feat/installer-catalog-picker` (2026-09-19).
**Scope:** the eight packages the installer catalog files under `menus-and-saves`, the three
arbitration mechanisms they share, and the names they ship under.
**Authorizes:** nothing yet. Each phase below is a separate change with its own gate and its own
runtime proof; phase 1 is a prerequisite of the rename and phase 4 is a prerequisite of every
deletion.

## 1. The measured state

Eight packages, 28 pairs, and more than half of those pairs cannot share a process.

| pair state | count | where |
|---|---:|---|
| declared `[[conflict]]` | 12 | `scripts/me3-dll-conflicts.toml` |
| undeclared but same mechanism | 3 | bd `er-effects-rs-tpqj` |
| genuinely co-loadable | 13 | mostly `er-build-import` and `er-save-picker` against the rest |

The duplication behind that number:

| package | src files | src lines | what it actually is |
|---|---:|---:|---|
| `er-quickload` | 132 | 59,155 | the product |
| `er-quit-rows` | 132 | 59,378 | a fork of the product: 60 files byte-identical, 63 drifted, 9 each side only |
| `er-quit-menu` | 1 | 215 | `RowSet::ALL` + two flows over `er-quit-menu-core` |
| `er-quit-load-character` | 1 | 288 | a subset of that `RowSet` + one flow |
| `er-save-game-row` | 1 | 280 | `RowSet::NONE` + `save_game_as` |
| `er-save-picker` | 1 | 234 | boot picker host; `included_in = ["er-quickload"]` |
| `er-save-disable` | 4 | 1,451 | write suppression, no rows |

`er-quit-menu`, `er-quit-load-character` and `er-save-game-row` are 783 lines between them over one
27,086-line feature crate, and they differ only in which `RowSet` bits they pass to
`er_quit_menu_core::row_cloner::arm` and which fields of `QuitMenuHost` they fill. Three cdylibs is
a costly way to spell an enum argument, and the cost is not notional: every pair among them is a
declared or undeclared conflict.

## 2. Why splitting further makes it worse

A statically linked crate's statics are **per cdylib**. Splitting a feature into its own DLL
therefore does not divide the state machine, it copies it. The repo has already paid for that
finding three times, and has built three different arbitration mechanisms in response:

| mechanism | what it arbitrates | how it elects | where |
|---|---|---|---|
| hook union | one MinHook instance per prologue | `GetModuleHandleA("er_quickload.dll")` then `GetProcAddress` | `crates/er-hook/src/lib.rs:813` |
| row registry | one row table per process | PEB module walk for `er_quit_rows_register`, lowest base wins | `crates/er-quit-menu-core/src/row_registry.rs:165` |
| overlay mutex | one Present-loop owner | named `CreateMutexW`, loser draws as a guest | `er-build-watermark-core` |

What none of them arbitrates is **feature state read back out of the wrong copy**. That is the
residual bug, measured on run `br-20260917-205031-edd3`: the row election worked, `er_quit_menu.dll`
won it, the product's `open_profile_load_dialog` came back `offered-but-already-held`, and then the
product's `system_quit_profile_load_activate_hook` read `flow_active` out of **its own** copy of the
core's statics, saw `false`, and forwarded to the game. The player pressed Load Character, picked a
slot, and got an in-world load onto the character already standing there.

An attach-time module probe is not arbitration at all. `er-save-picker` stands down by calling
`GetModuleHandleA("er_quickload.dll")` inside `DllMain`, and ME3 attaches `[[natives]]` in profile
order, so the guard only fires when the product happens to be listed first. Measured 2026-09-19: the
installer sorted by display label, `Boot save picker` sorted above `Quickload (full suite)`, and the
guard was defeated in every generated install.

## 3. The hub is a hard-coded file name, and one shipped `[[shared]]` row is already wrong

`er-hook` finds the union by the literal `er_quickload.dll` (`PRODUCT_DLL_NAME`). Ten crates outside
`er-hook` reach the hub through it:

```
er-armament-icons  er-diag-harness  er-dinput-suppress-core  er-enemynpc-effects
er-hotkey-conflicts  er-input-harness  er-net-effects  er-quit-menu-core
er-refill-all  er-reload-trace
```

Two consequences, and the second is a live defect rather than a design opinion:

1. **The product DLL cannot be renamed** while the hub is looked up by file name. The export name
   `er_effects_union_register` is deliberately an ABI and must not move; the *module* name has
   become one by accident, which nothing says and nothing gates.
2. **`[[shared]] er-quit-rows + er-armament-icons` claims a mechanism that cannot engage.** Its
   `reason` says "whichever shell reaches the prologue first creates the dispatcher and the other
   chains into it". `er-quit-rows` does export `er_effects_union_register`
   (`crates/er-quit-rows/src/mh.rs:47`), but `er-armament-icons` reaches the hub through
   `er_hook::register_shared_hook` (`gfx_equip_hook.rs:744`), which looks for `er_quickload.dll` and
   nothing else. In a profile carrying `er_quit_rows.dll` and `er_armament_icons.dll` with no
   product, the companion takes `HookRoute::LocalUnion`, and two MinHook instances land on
   `TITLE_SCALEFORM_FILE_OPEN_RVA` -- the exact configuration measured on 2026-08-23, where the
   product reported `file_open_observer_installed = true` with `file_open_hits = 0` for a whole
   session. The row does record that the pair has never co-loaded and says to measure it before that
   changes; the mechanism sentence is still false as written.

So the hub generalisation is not cosmetic groundwork for the rename. It is the fix for a row that
currently licenses a co-load the code cannot honour.

## 4. The plan

Six phases. Each is independently shippable, and the ordering constraints are real: 1 before 5,
4 before 6.

### Phase 0 -- declare what is already true (bd `er-effects-rs-tpqj`)

Add the three undeclared pairs to `scripts/me3-dll-conflicts.toml` and regenerate
`tools/er-installer/src/catalog.rs` with `scripts/gen-installer-catalog.py`:

| a | b | kind | mechanism |
|---|---|---|---|
| `er-quit-menu` | `er-save-game-row` | `duplicate-owner` | two derivers of the `02_040` Quit grid, and two bare `MhHook::new` owners of the profile-row populate prologues |
| `er-quit-load-character` | `er-save-game-row` | `duplicate-owner` | same |
| `er-quit-rows` | `er-save-disable` | `hook-collision` | both call `er_save_suppress::install`, which takes `0xe6fb50` and `0xe6e430` with a bare `MhHook::new` |

Landed 2026-09-19 on branch `phase0/declare-menus-and-saves-conflicts` (`c6f30075`), and the first
two rows do not say what this plan originally said they would. The drafted mechanism -- "two copies
of `er-quit-menu-core`, both `install_host` + `arm`" -- does not survive reading the sites:
`install_host` is per-cdylib by design and every shell does it, and `row_cloner::arm`
(`row_cloner.rs:1591`) elects one owner through `row_registry` and makes the loser register through
the winner's `er_quit_rows_register`, merging into a single table with first-come-wins per flow
slot. The rows are disjoint as well -- `RowSet::ALL` carries `save_game_as: false` deliberately
(`row_cloner.rs:184`) and `er-save-game-row` arms `save_game_as` alone -- and so are the flows.

What does collide is written in the core's own doc comments. Both shells derive the six-cell
`02_040` grid, and `gfx_swap.rs` already says that two derivers is not a race to be won because
`quit6` fail-closes on already-derived input. Both call
`profile_row_chrome::install_profile_row_populate_hooks` (`profile_row_chrome.rs:595`), a bare
`MhHook::new` on `0x8757e0` and `0x951220` whose doc says one owner per process is a property of the
conflict table rather than an assumption the code makes. And with no `er_quickload.dll` in the
process, `register_shared_hook` returns `HookRoute::LocalUnion` for both.

Two neighbouring pieces of prose asserted the opposite and had to move with the rows: the comment
block in `scripts/me3-dll-conflicts.toml` called two of these pairs deliberately absent, and
`scripts/check-quit-row-flow-overlap.py`'s docstring repeated it. That gate's logic is untouched --
it demands declarations for flow-sharing pairs and never forbids extra ones -- but a silent pass
from it is not a statement that a pair is co-loadable.

Also correct the `reason` on `[[shared]] er-quit-rows + er-armament-icons` to say what section 3
measured: the companion cannot find that hub today. Either downgrade the row to `[[conflict]]` until
phase 1 lands, or land phase 1 first and keep the row.

No runtime proof needed; the gates are `check-me3-dll-conflicts.py` and
`gen-installer-catalog.py --check`.

### Phase 1 -- make the union hub name-free

Replace the fixed-name lookup in `er-hook` with the election `row_registry` already proves works:

* walk the loaded module list, ask each for `er_effects_union_register` / `er_effects_union_register5`;
* if exactly one module exports it, that is the hub;
* if several do, the **lowest module base** wins -- the same deterministic rule `row_registry::elect`
  uses, so every caller computes the same answer with no shared state;
* keep `GetModuleHandleA("er_quickload.dll")` as the **first** probe, unconditionally, because users
  install these DLLs one at a time and an already-downloaded companion built against the old
  behaviour must keep working -- and as implemented that probe is *decisive*, not a fall-through:
  found-and-not-us delegates, found-and-it-is-us takes the local union and skips the election
  entirely, and only absent-or-wrong-arity elects. The middle arm is load-bearing. If the product
  ran the election it could elect another hub-capable shell whose own named probe points back at
  the product, and the two would cross-register;
* keep the self-resolution guard (`hmod as usize != dll_base()`) so the hub does not route its own
  registration out through a C-ABI round trip. As implemented this is a test on the *winner*, not a
  filter on the candidate list, and the distinction is the whole safety property: filtering self out
  of the input inverts the failure rather than preventing it, because the lowest-based hub would
  then elect the second-lowest while that one elected it back, each filing handlers in the other's
  table and putting two MinHook instances on one prologue.

Landed 2026-09-19 on branch `phase1/elect-union-hub` (`f0ec135e`). The rule is
`er_hook::elect_union_host(candidates, self_base) -> Option<usize>`, a pure function with no `cfg`
at `crates/er-hook/src/lib.rs:906`; `loaded_module_bases()` moved to `er-hook` verbatim and
`row_registry` now calls it. 35 host tests pass, including one that encodes the shipped broken pair
and asserts from both sides. The runtime proof below has not been run.

Reuse `row_registry`'s `loaded_module_bases()` rather than writing a second PEB walk -- lift it into
`er-hook` and have `row_registry` call it, since `er-quit-menu-core` already depends on `er-hook`.

**Gate:** a host-side test over the election rule (lowest base, self-exclusion, single exporter,
no exporter), plus a line in `check-shared-hook-rvas.py`'s prose recording that the hub is now
elected rather than named. **Runtime proof:** a profile with `er_quit_rows.dll` +
`er_armament_icons.dll` and no product, where the companion logs `HookRoute::ProductUnion` and
`file_open_hits > 0`. That proof is what closes section 3's defect.

### Phase 2 -- one row shell, config-selected

Merge `er-quit-menu`, `er-quit-load-character` and `er-save-game-row` into a single cdylib. The
merged shell reads its row set from its own config file rather than from a cargo feature, because a
cargo feature cannot be changed by a player and the installer would have to ship three builds:

```toml
# er-quit-rows.toml, beside the game exe
rows = ["load-character", "load-character-from-file", "save-game"]
# "load-build-from-url" and "generate-build-link" also available
```

* `RowSet` is built from that list at arm time; an absent row is never cloned, which is the existing
  guarantee that a press cannot reach a flow the host does not supply.
* `save_game_start_flow` vs `save_game_as_start_flow` stays the existing `hijack-quit-row` decision,
  moved into the same config file as `save_game = "add-row" | "replace-native-row"`.
* The three `[[conflict]]` rows among the merged shells disappear because the packages do.
* Every row that currently needs a flow the standalone does not have keeps behaving as it does now
  until phase 4; this phase moves no behaviour, only the packaging.

**Gate:** host tests for the config -> `RowSet` mapping, including the empty and unknown-name cases.
**Runtime proof:** each row set armed once and pressed, against the existing measured behaviours.

Landed 2026-09-20. The merged shell keeps the `er-quit-menu` name and its config file is
`er-quit-menu.toml`, so the DLL and the file a player edits are named the same thing the way
`er_quickload.dll` and `er-quickload.toml` are; phase 5 renames both together with everything else.
`er-quit-load-character` and `er-save-game-row` are deleted, and with them five `[[conflict]]` rows
whose packages no longer exist -- two of which are kept as prose in
`scripts/me3-dll-conflicts.toml`, because what they measured is still true of any future pair.
`er-quit-menu` inherits the two `er-save-game-row` held against `er-reload-trace` and
`er-save-disable`: both mechanisms live in `er-quit-menu-core`, which it links.

Three things moved that the phase description did not anticipate, all in
`er_quit_menu_core::arm::arm_standalone`, because it is now the only arm path and `er-save-game-row`
hand-rolled its own:

* It installs the Save Game row's text hook and stage task, and reports them in `StandaloneArm`.
* It installs the browser's `ProfileLoadDialog` activation. Only `er-save-game-row` did, so a
  `load-character-from-file` press on the old `er-quit-menu` reached vanilla's own OK handler --
  the `Start with selected profile` confirm over a folder measured on br-20260912-203044-5fbd.
* It derives the `GfxServeSet` from the row set instead of serving `ALL_PICKER_KEYED` for every
  caller. That is what lets `save_game = "replace-native-row"` arm `RowSet::NONE` without widening
  the two-cell Quit grid to six (bd `slim-quickload-still-widened-the-quit-grid-2026-09-12`), and
  it stops a character-only configuration serving the link field's movie it never opens.

**Proven at runtime as far as the arm, both ends of the config space**, on the shell's own log with
no product in the profile:

| run | `er-quit-menu.toml` | what the DLL reported |
|---|---|---|
| br-20260920-170312-4b11 | absent | `config: auto-created ...\Game\er-quit-menu.toml with the defaults`, then `arming [LoadProfile, LoadSaveProfiles, LoadBuildFromUrl, GenerateBuildLink] (save_game=add-row, stated_in_the_file=true)` with no complaint line -- so the file it writes is a file it reads back |
| br-20260920-170312-4b11 | (same) | `serve=GfxServeSet { quit_grid: true, build_url_field: true, path_editor_field: true, profile_select: false, profile_select_picker_key: true }` -- identical to the `ALL_PICKER_KEYED` this configuration used to get, so the four-row default did not move |
| br-20260920-170351-44e2 | `rows = ["save-game"]`, `save_game = "replace-native-row"` | `arming [SaveGameAs] (save_game=replace-native-row)`, `serve=GfxServeSet { quit_grid: false, ... }`, `save_game_start_flow=claimed save_game_as_start_flow=absent`, `save_flow_task=yes` |

Both runs reported `standalone arm complete=true ... picker_activate=yes`. The third row is the one
the derivation was for: a host that clones nothing no longer widens the two-cell Quit grid, and its
serve line names only `the path editor's movie + the 05_010 picker movie` where the four-row run
also names `the six-cell Quit grid + the link field's movie`.

**A row PRESS is still unproven**, which is the rest of this phase's runtime proof. It needs the
Quit tab on screen and the agent drives every input, and `--harness-drive menureload` derails at
`tab_to_quit`. Frida settled why on run br-20260920-165749-f063: `_SettingTabControl`'s virtual is
pumped 234 times with its grid readable and its cursor at 0, `GridControl::Update` runs 1170 times,
and not one cursor moves -- the consumer runs and the press never arrives, because the phase taps
`inputmgr+0x90`, which `game_mem.rs:672` and AGENTS.md both record as a shown-menu-window bitmap
rather than input. bd `er-effects-rs-9vyy` carries it and names the route the repo already has:
`menu_code_pad_binding` says which pad input the game itself has bound to each menu action.

### Phase 3 -- retire the `er-quit-rows` fork

`er-quit-rows` was created as "a copy of the product reduced to the Quit rows, gated not deleted"
(`28ca8f89`). 22 commits later it is 63 diverged files against the product and is itself a hub that
no companion can find. Two ways to end it, and the first is the one to take:

1. **Delete it** and let phase 2's merged shell be the no-product build. Everything `er-quit-rows`
   carries that the merged shell does not is either the product's job (autoload, the loading cover,
   the portrait) or already feature-gated off in its default set (`build-rows`).
2. Finish the reduction and make the product depend on it. This is the strictly larger job: the 63
   drifted files have to be reconciled in a direction nobody has chosen, and every drift is a
   potential behaviour change in the product.

**Landed 2026-09-20.** The package is deleted: 136 files, and with them the four `[[conflict]]` /
`[[shared]]` rows it held, its catalog row, its half of `check.sh`'s host-test line and of
`check-rust-build.sh`'s `me3_shells` array, 26 entries in
`scripts/audit-1170-gate-bypass.baseline.json` and 83 rows in `scripts/rva-alias-allowlist.txt`.
Every deleted row is either restated in the product's own row or recorded as prose beside where it
was, because the measurement usually outlives the package: the tracer pair, the keystate pair and
the save-suppress pair are all `er-quickload` statements that the fork inherited by copying that
source, and the one sentence that was about shells rather than about this package -- two Quit-grid
derivers cannot share a process, because `quit6` fail-closes on already-derived bytes -- is kept
against `er-quickload` + `er-quit-menu`.

Two findings the deletion produced, neither of them anticipated here:

* **The workspace now has exactly one hub.** `crates/er-quickload/src/mh.rs:52` is the only
  definition of `er_effects_union_register` left; the fork was the second exporter, and
  `er-quit-menu` is a shell over `er-quit-menu-core`, which resolves the registrar and never
  provides one. So phase 1's runtime proof named a profile that cannot be built any more, and in a
  product-less profile every companion still takes `HookRoute::LocalUnion`. The election itself is
  unaffected and stays host-tested. Making the merged shell export the registrar is the fix when it
  is wanted -- a hub is an export, not a package.
* **`boot_view_clock.rs` moved to `er-telemetry-core`, not into `er-quickload`.** The sweep left the
  destination open between the two. The telemetry crate already owns `BOOT_VIEW_EPOCH_SEQ` /
  `BOOT_VIEW_EPOCH_KIND` and has described this clock from the outside at
  `counters/loading_cover.rs:349` since 2026-08-22, and the whole reason the fork extracted it was
  that leaving the clock inside the cover module made `loading-cover` a feature seven unrelated
  callers depended on. Lifting it out of `boot_progress.rs` within the same crate would have kept
  the clock in the crate whose feature set is the problem; the crate move ends it. The product
  re-exports both readers from `boot_progress.rs`, so `crate::experiments::boot_view_epoch_ms`
  resolves exactly as before and no caller changed.

The sweep below is what the deletion commit carried. It found nine files that existed only in
`er-quit-rows`:

```
experiments/boot_view_clock.rs
experiments/lifecycle/save_flow.rs
experiments/startup_hooks/quit_menu/profile_05_010_editor_runtime.rs
experiments/startup_hooks/quit_menu/save_dest_commit.rs
experiments/startup_hooks/quit_menu/save_flow_boxes.rs
experiments/startup_hooks/quit_menu/save_picker_menu.rs
experiments/startup_hooks/quit_menu/save_picker_path_editor.rs
experiments/startup_hooks/quit_menu/system_quit_ownership_repro.rs
menu_window_run_install.rs
```

Each one is either dead with the fork, or a port into `er-quit-menu-core`. Decide per file in the
deletion change, in the commit message, not afterwards.

### Phase 4 -- flow parity, so the election stops mattering (bd `er-effects-rs-ye3q`)

The residual cross-copy bug in section 2 survives every packaging change above, because it is not a
packaging bug: two hosts supply the same flow slot, one wins, and the loser's detour reads
`flow_active` from its own statics. Two candidate fixes, and they are not equivalent:

* **Parity.** Give the standalone shell the product's save-safe switch, so whichever host wins the
  election does the same thing. This is `er-effects-rs-ye3q`, and it is the one the conflict table
  already points at.
* **Delegation of state, not just of rows.** Extend `row_registry` so the owner also publishes the
  flow-state reads (`flow_active` and its siblings) as exports, and have every host read them
  through the owner rather than out of its own copy. This generalises: it is the same shape as the
  hook union, applied to statics instead of prologues.

Parity is the smaller change and is already tracked. Delegation is the one that makes future splits
safe, and phase 6's deletions do not depend on either.

### Phase 5 -- rename, once, after phase 1

Renames are user-visible: a `.me3` profile names DLL files, and the installer writes those profiles.
So this happens once, in one change, with the installer regenerated in the same commit.

| now | after | why |
|---|---|---|
| `er-quickload` / `er_quickload.dll` | `er-menus-and-saves` / `er_menus_and_saves.dll` | "quickload" names one early feature of a suite that is now the Quit rows, the boot picker, the loading cover and the portrait; the catalog already files it under Menus and saves |
| `er-quit-menu-core` | `er-quit-rows-core` | the core is named after a shell that phase 2 deletes; name it after the surface it owns |
| `er-quit-menu` (the merged shell, and its `er-quit-menu.toml`) | `er-quit-rows` / `er_quit_rows.dll`, with `er-quit-rows.toml` beside it | the merged shell takes the name the fork vacates in phase 3, and the name is finally accurate. The config file is renamed in the same commit: the DLL and the file a player edits are named the same thing, and splitting the rename would leave `er_quit_rows.dll` reading `er-quit-menu.toml` |
| `er-save-picker`, `er-save-disable`, `er-build-import` | unchanged | each says what it is |

Hard constraints on the rename, all of them measured:

* **`er_effects_union_register` and `er_effects_union_register5` do not move.** They are resolved by
  string from seven other images, and `er-hook`'s comment already records why the `er_effects_`
  prefix survived the 2026-08-26 crate rename.
* **`er_quit_rows_register` / `er_quit_rows_armed` do not move** either, for the same reason: they
  are the row registry's ABI.
* **Phase 1 must be in the same release or earlier.** Renaming `er_quickload.dll` before the hub is
  elected rather than named orphans every companion at once.
* Keep a `[[natives]]`-level note in the installer's release notes: an existing hand-written profile
  naming `er_quickload.dll` stops resolving, and the installer overwrites profiles rather than
  migrating them.

### Phase 6 -- the catalog after consolidation

| catalog row | fate |
|---|---|
| Quickload (full suite) | stays, relabelled to the new name |
| Quit-menu rows only | the merged row; relabelled with the package in phase 5 |
| Load Character rows only | merged into it, 2026-09-20 |
| Save Game row | merged into it, 2026-09-20, selected in `er-quit-menu.toml` |
| Quit-menu rows (trimmed build) | removed with the fork, 2026-09-20 |
| Boot save picker | stays; `included_in` stays a refusal until its `DllMain` probe is replaced by a real election |
| Disable saving | stays |

Four player-facing rows become two, and both halves have landed: phase 2's merges took the category
from eight packages to six and from 28 pairs to 15, and phase 3's deletion took it to five packages
and 10 pairs on 2026-09-20. The table then held 9 `[[conflict]]` and 8 `[[shared]]` rows over 28
shipped shells, all classified.

## 5. What this plan deliberately does not do

* It does not split `er-quickload`'s features into their own DLLs. The features already exist as
  cargo features (`quit-rows`, `loading-cover`, `portrait`, `autoload`), which is the seam that costs
  nothing; a DLL per feature costs a copy of the state machine each.
* It does not touch `er-save-disable` or `er-build-import`. Both are genuine components with their
  own mechanisms, and neither arms a row.
* It does not merge `er-save-picker` into the product. A real election (phase 1's shape, applied to
  the picker's host) would make it co-loadable, which is better than either merging or refusing --
  but that is its own change and nothing above depends on it.

## 6. Cleanup that falls out on the way

* `save-picker` is declared `save-picker = []` on both `er-quickload` and `er-quit-rows` and gates
  zero `cfg` sites in either. Deleted 2026-09-19 on branch `cleanup/dead-feature-and-deps`
  (`c5e7e2c4`). What actually decides the boot picker is `autoload`, confirmed at
  `er-quit-rows/src/experiments/gpu_readback/save_picker_overlay.rs:30`, and `er-quit-rows` does
  not carry it by default. Three consumers named the feature by string and had to move with it:
  `scripts/quickload-feature-bite.baseline.json`, `scripts/check.sh:2700` and
  `scripts/gate-quickload-quit-rows-deadcode.py:32` -- the last two pass it on a `--features` line,
  which cargo rejects outright once the feature is gone.
* `er-quit-rows` names `features = ["boot-flow", "os-dialog"]` on its `er-save-picker-core`
  dependency unconditionally. This bullet was wrong about `boot-flow`, and the correction is the
  point of keeping it. Refuted 2026-09-19 by dropping the feature and building: three errors,
  `E0432` and `E0433`, `cannot find overlay in er_save_picker_core`, with rustc's own note that the
  item is gated behind `boot-flow`. It gates `pub mod overlay` at
  `er-save-picker-core/src/lib.rs:92`, and `experiments/gpu_readback/save_picker_overlay.rs:5` is an
  unconditional `pub(crate) use` of it under an unconditional `mod gpu_readback`. Unreachable at
  runtime without `autoload` is a different statement from unreachable at link time, and only the
  second one licenses removing a feature. `os-dialog` is load-bearing, and also redundant on that
  edge today because `er-quit-menu-core` requests it and cargo unifies -- left declared, so
  `er-quit-rows` does not silently ride a transitive crate's request.
* `er-quit-rows` depends on `er-build-import-runtime` unconditionally, and a `--features build-rows`
  build would be a duplicate owner against `er-build-import` while the conflict table has no way to
  say "conflicts only in this configuration". Phase 3 removes this instance of the question but not
  the expressiveness gap; that is bd `er-effects-rs-38gi`. The mechanism sentence needed correcting
  too: linkage is not the discriminator, because `er-quit-rows`' own sources name neither import
  crate and reach both through `er-quit-menu-core`, which uses `er_build_import_runtime`
  unconditionally and has no features at all. What is actually configuration-conditional is the pair
  of `CSTaskImp` FrameBegin tasks at `lib_parts/dll_entry_parts/task_registration.rs:687` and `:702`,
  both behind `build-rows` -- the same mechanism the table's `er-quickload` against
  `er-build-import` row already records.
