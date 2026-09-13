# er-invasion-warp: banner lifecycle and the settings surface

Investigation-only. Nothing was built, nothing was launched, no runtime probe ran. Every claim below
is tagged **VERIFIED** (read out of the tree at the cited line) or **HYPOTHESIS** (needs a named
measurement, which is stated).

Four asks, in the order they were given:

1. Move the TOML settings onto a `hudhook` in-game UI.
2. The banner is not always showing up; it should be present the whole time and clear its text after
   each event.
3. Invading locally produces the banner later than a non-local match does.
4. Stop the "No Invasions" message from popping up more than once.

Items 1 and 2 turn out to be the same piece of work, and item 4 is the only one that is blocked.

---

## Cross-cutting facts worth reading first

**The live config on this machine** (`~/.local/share/Steam/steamapps/common/ELDEN RING/Game/er-invasion-warp.toml`, read 2026-09-13):

```toml
enabled = true      mode = "exact"        hunt = false        reject_notice = true
map_pins = true     steam_hooks = true    dll_users_only = false
ersc_observers = false                    ersc_show_observer = false
ersc_lobby_key_observer = false           ersc_invade_observer = true
```

`ersc_observers = false` is the master gate at `crates/er-invasion-warp/src/local_invasion_filter.rs:2500-2503`,
so **all three** ERSC observers are withheld despite `ersc_invade_observer = true`. That is deliberate
(bd `detouring-ersc-show-reproduces-the-0x10043-crash-at-295s-2026-09-09`; the `[compatible]` demotion at
`scripts/me3-dll-conflicts.toml:645-684` rests on it), and it has a consequence that shows up in three of
the four items: **`OSM` -- Seamless's option-menu object -- is never captured from an ERSC seam.** The only
live route left is the game-side `OpenConversationChoicesMenu` detour in
`crates/er-invasion-warp/src/lynchpin_use.rs:285-292` + `:347-350`, which adopts `SHOW_R14 - 0x120`, and the
`er_invasion_warp_adopt_menu_object` export (`crates/er-invasion-warp/src/lib.rs:454`).

**Do not detour `ersc.dll`.** Two measured failures, both recorded:
* bd `detouring-ersc-show-reproduces-the-0x10043-crash-at-295s-2026-09-09` -- the `show` detour reproduces
  `access-violation game+0x11f42` at +29.5 s, then 215 more, marching the stack down. On screen it is a
  load softlock.
* bd `invasion-warp-cancel-dies-cpp-throw-crosses-extern-system-2026-09-08` -- the ERSC cancel action throws
  a C++ `std::system_error`, which unwinds out of our `unsafe extern "system"` (a nounwind ABI) into Rust's
  forced-abort landing pad and terminates the process with no crash record.

Anything below that needs to reach into Seamless does it by reading, by a game-side detour, or not at all.

---

## Item 1 -- TOML settings on a hudhook UI

### Where the config lives today (VERIFIED)

| what | where |
|---|---|
| file name, beside the DLL | `crates/er-invasion-warp-core/src/local_invasion_config.rs:38` (`CONFIG_FILE_NAME = "er-invasion-warp.toml"`) |
| section name | `local_invasion_config.rs:41` (`SECTION_NAME = "local_invasion"`) |
| shipped file + all user documentation | `local_invasion_config.rs:45-226` (`DEFAULT_CONFIG_TOML`) |
| parser (hand-rolled, no `toml` crate) | `local_invasion_config.rs:298-507` |
| writer | `local_invasion_config.rs:605` (`render_local_invasion_config`) |
| hot reload, content-polled ~1 s | `local_invasion_config.rs:839` (`HotConfig::reload_if_changed`) |
| **write-back** | `local_invasion_config.rs:800-813` (`HotConfig::save`) |
| process-wide holder | `local_invasion_filter.rs:284` (`static CONFIG`), `:325` `config_path()`, `:333` `ensure_config_file()`, `:350` `refresh_config()`, `:466` `current_config()`, `:1601` `current_config_snapshot()` |

The 21 keys: `enabled`, `mode` (`exact` / `area` / `named`), `hunt`, `reject_notice`, `map_pins`,
`steam_hooks`, `ersc_observers`, `ersc_show_observer`, `ersc_lobby_key_observer`, `ersc_invade_observer`,
`dll_users_only`, `named_locations`, `allowed_blocks`, `blocked_blocks`, `named_location_text_ids`,
`mark_key`, `unmark_key`, `enable_toggle_key`, `warp_nearest_key`, `warp_next_key`, `warp_other_area_key`.

**An in-game mutation writing back to the TOML is not a new mechanism -- it is the existing one.**
`crates/er-invasion-warp/src/local_invasion_filter/hotkeys.rs:189-198` (`apply_enable_toggle`) already flips
`enabled` from a key press and calls `hot.save(&path, &config)`; `:229-268` does the same for the
mark/unmark lists. `save` writes, re-reads, re-parses, adopts the text into the watcher so our own write is
not re-reported as a user edit, and returns whether the round trip matched. So the answer to "would
settings written from an in-game UI need to persist back to the TOML" is **yes, and through
`HotConfig::save`, unconditionally** -- anything held only in memory is clobbered by the next hot reload.

### The hudhook infrastructure that already exists (VERIFIED)

hudhook 0.9.2 is **vendored and patched** at `third_party/hudhook`, wired by `[patch.crates-io]` in the
workspace manifest, because stock `Pipeline::new` aborts Elden Ring on Wine/Proton + vkd3d
(bd `hudhook-pipeline-new-aborts-on-dead-hwnd-vkd3d-2026-08-29`). Two patches: `util::try_win_size` returns
`Option` instead of unwrapping, and `Pipeline::new` returns `Err(E_HANDLE)`.

**The reusable pattern is `er_build_watermark_core::overlay_host`, and it is not optional.**
`crates/er-build-watermark-core/src/overlay_host.rs`:

* `OverlayFrame` (`:70-82`) -- `ui`, `imgui_context`, `alloc_func`, `free_func`, `alloc_user_data`.
* `OVERLAY_ABI_TAG = 0x0903` (`:90`); export name `er_overlay_register_guest_v1` (`:94`).
* `designate_host()` / `become_host()` / `is_host()` (`:121-139`), `register_guest` (`:161`),
  `dispatch_guests` (`:186`), `register_with_host` (`:229`), `register_with_host_retrying` (`:268`),
  `adopt_frame` (`:287`), `export_overlay_host!` (`:322`).
* The owner is decided by a named mutex in `crates/er-build-watermark-core/src/overlay.rs:156`
  (`claim_owner`) / `:172` (`claim_overlay_ownership`).

Four shells already ride it: `er-build-watermark`, `er-net-effects`, `er-invasion-path`, `er-npc-possess`.

**Which of the five named crates is the template: `crates/er-invasion-path/src/render.rs`** (330 lines). It
is the smallest complete host-or-guest implementation -- `guest_draw` at `:155`, `impl ImguiRenderLoop` at
`:168`, the `Hudhook::builder()...apply()` install at `:232`, and one `draw(ui)` taken identically on both
paths so the guest case cannot drift. `crates/er-npc-possess/src/overlay.rs` is the same shape with a
panel. `crates/er-quickload/src/experiments/present_overlay.rs` is **not** a template: bd
`no-readd-hudhook-2026-06-28` bans hudhook in the product specifically. That ban is about `er-quickload`,
not about companion shells.

### Can two DLLs each own a hudhook Present hook in one profile?

**No -- and that is precisely what the arbitration exists to prevent** (VERIFIED, bd
`imgui-context-is-per-dll-overlay-host-guest-2026-08-25`). Two `Hudhook::apply()` calls in one process
double-hook `Present` and **the second loses silently**: it logs "hudhook dx12 overlay installed" and then
its render count stays 0 forever, with no error anywhere. me3 loads natives in profile order, so the winner
is decided by alphabet. A second imgui context is the other half of the same trap -- imgui's context is a
per-DLL global, so a guest handed a bare `&Ui` dereferences a null `GImGui` and dies on the first
`ui.io()`; `adopt_frame` exists to install the host's context *and* allocators before anything touches `ui`.

So the rule for er-invasion-warp is: **never call `Hudhook::builder().apply()` unconditionally.** Claim
through `claim_overlay_ownership()`; host if you win, `register_with_host_retrying(guest_draw)` if you lose.

`scripts/check-me3-dll-conflicts.py` proves the conflict table *covers* every shell in `me3_shells`
(`scripts/check-rust-build.sh`, where `er-invasion-warp:er_invasion_warp` is listed). It does **not** prove
the table's prose is true. `er-invasion-warp`'s `[compatible]` entry at
`scripts/me3-dll-conflicts.toml:645-684` currently asserts the shell "installs no D3D12 Present or
compositor hook" -- adding an overlay makes that sentence false and no gate will catch it. Updating it is
part of the work, not a footnote. (`crates/er-invasion-path/Cargo.toml` already depends on
`er-invasion-warp-core` for the keybind table, so the two crates are not strangers.)

### The real obstacle: input (VERIFIED)

`MessageFilter` is a method on the `ImguiRenderLoop` trait -- `crates/er-net-effects/src/present_overlay.rs:239` --
so **only the HOST can ask for WndProc messages to be filtered**. `OverlayFrame` carries `ui`, a context and
three allocator fields, and nothing about input. A guest therefore has no way to keep a click off the game.

And filtering WndProc is not sufficient anyway: Elden Ring reads the mouse through DirectInput, which never
touches the window procedure, so a click on an overlay button *also* becomes an attack unless it is blanked
in the DirectInput state. `er-net-effects` owns that (`present_overlay.rs:308`,
`input_suppression::set_pointer_over_overlay`), behind the shared `IDirectInputDevice8::GetDeviceState`
union hook recorded in `scripts/me3-dll-conflicts.toml`.

`er-invasion-warp` already polls raw keys on the game thread (`local_invasion_filter/hotkeys.rs`,
`er-invasion-warp-core/src/keybind.rs`) and already writes the TOML from a key press. **A keyboard-driven
panel needs no new input plumbing at all; a point-and-click one needs either host status or an ABI change.**

### Full coverage: every key, and whether it is live mid-session (VERIFIED)

The requirement is that the UI expose **every** setting, and that anything which cannot be changed at
runtime be named rather than quietly dropped. Here is every key, its consumption site, and its liveness.

| key | read at | live mid-session? |
|---|---|---|
| `enabled` | `local_invasion_filter.rs:1769`, per match, via `current_config()` `:466` | **yes, both ways** |
| `mode` | `local_invasion_filter.rs:1816` `config.judge(...)`, per match | **yes, both ways** |
| `reject_notice` | `local_invasion_filter.rs:1772`/`:1876`/`:2837`, per event | **yes, both ways** |
| `hunt` | `lobby_publish.rs:1189-1202` `hunt_target()`, per outgoing query | **yes, both ways** (needs `steam_hooks` to have armed the hook once) |
| `dll_users_only` | `lobby_publish.rs:918-968` `reapply_pool_if_toggled()`, every tick from `lib.rs:271` | **yes, both ways** -- it compares `POOL_APPLIED` against the config and re-advertises on a change. This is the one key already engineered to be toggled live |
| `allowed_blocks` | per match, through `judge` | **yes, both ways** |
| `blocked_blocks` | per match, through `judge` | **yes, both ways** |
| `named_location_text_ids` | per match, through `judge` | **yes, both ways** |
| `mark_key`, `unmark_key` | `hotkeys.rs:127-139`, re-read every poll, latches re-seated on a rebind | **yes, both ways** |
| `enable_toggle_key` | `hotkeys.rs:38` `enable_toggle_key_in_force()`, every poll | **yes, both ways** |
| `warp_nearest_key`, `warp_next_key`, `warp_other_area_key` | `local_invasion_filter.rs:111` `warp_keys_in_force`, per poll | **yes, both ways** |
| `named_locations` | parsed and then ignored | **not implemented at all** -- `DEFAULT_CONFIG_TOML:155-163` says so: turning a typed place name into the FMG text id has not been reversed. Show it in the UI read-only, with the same sentence the TOML carries, or the UI will look broken |
| `map_pins` | `lib.rs:219-234` | **ARM-ONLY.** `false -> true` arms the two map hooks on the next tick; `true -> false` only stops re-arming. `lib.rs:214-218` states this in the source. Worse, `map_live_pins::restyle_live_pins` / `top_up_live_pins` (`lib.rs:323-335`) are not gated by the key at all, so pins keep drawing and re-colouring after it is turned off |
| `steam_hooks` | `lib.rs:282-290` | **ARM-ONLY.** The three installers latch on `SET_HOOK_INSTALLED` / `HUNT_HOOK_INSTALLED` / `FILTER_HOOK_INSTALLED` (`lobby_publish.rs:1047`, `:1206`, `:1002`) |
| `ersc_observers`, `ersc_show_observer`, `ersc_lobby_key_observer`, `ersc_invade_observer` | `local_invasion_filter.rs:2500-2527` | **ARM-ONLY**, same shape (`SHOW_HOOK_INSTALLED` `:634`, `LOBBY_KEY_HOOK_INSTALLED` `:868`, `INVADE_HOOK_INSTALLED` `menu_object.rs:105`) |

**What it would take to make the arm-only six fully live.** MinHook can disable a detour without
uninstalling it (`MH_DisableHook` / the queued `MH_QueueDisableHook` + `MH_ApplyQueued` the installs already
use), so the mechanism exists. What does not exist is a place to keep the handle: every installer today
drops its `MhHook` and keeps only the trampoline in an `AtomicUsize`, and `er_hook::register_union_hook`
registers into a shared dispatcher with no per-handler disable. Two honest options, and they differ a lot in
cost:

* **Cheap and correct for five of the six:** add a runtime *gate* inside each handler rather than
  uninstalling. The detour stays, and its first line asks the config whether it should act. That makes
  `map_pins`, `steam_hooks` and the three `ersc_*` keys behave live in both directions with no MinHook API
  work, at the cost of one config read per call. `map_pins` also needs `restyle_live_pins` /
  `top_up_live_pins` gated, which they are not today.
* **Not worth it:** genuine uninstall. `er-hook`'s union would need per-handler enable/disable, and detour
  removal on a Themida-adjacent target is exactly the category that has already killed this process twice.

**Until that gating lands, the UI must label those six "takes effect on arm; a restart to disarm" rather
than pretending.** An arm-only toggle that silently does nothing is the failure mode this whole codebase
keeps re-learning (a hook reporting `installed = true, hits = 0`).

### The shared-state shape: who owns the config, and how it is read without tearing

**There is no tearing today and the design must not introduce one.** `CONFIG` is a
`Mutex<Option<HotConfig>>` (`local_invasion_filter.rs:284`). `current_config()` (`:466`) and
`current_config_snapshot()` (`:1601`) take that lock and return an **owned clone**, so every reader already
works on a private copy. The hazard a UI adds is not a torn read; it is **doing file I/O and lock
acquisition inside `Present`**, because `HotConfig::save` does an `fs::write` followed by a
`read_to_string` (`local_invasion_config.rs:805-812`) and hudhook's render runs on the render thread inside
`Present`.

**The pattern to name is `er-npc-possess`'s creature picker, and it is exactly this problem already solved.**
Three crates in this workspace have both an overlay and a hot config -- `er-invasion-path`,
`er-net-effects`, `er-npc-possess` -- and **none of them writes its config from the overlay**, so there is no
prior art for live config *editing*. There is prior art for the half that matters:

* **The game thread owns the state and the logic; the render thread only reads a snapshot.**
  `crates/er-npc-possess/src/picker/mod.rs:271` `tick(settings, pad_buttons)` runs on the game task, reads
  the keys, moves the cursor and **republishes** a `View`. `picker/mod.rs:243` `view()` hands the renderer a
  `clone()` of that snapshot; `crates/er-npc-possess/src/picker/render.rs:57` `draw(ui)` does nothing else.
* **A lock-free fast path guards the common frame.** `picker/mod.rs:249` `is_drawing()` is an `AtomicBool`
  read, so the closed case -- almost every frame -- never touches the mutex. `render.rs`'s own comment says
  why: taking the picker mutex 60-144 times a second to be told "closed" would contend with the game
  thread's per-frame tick for nothing.
* **A generation counter tells a reader when to re-snapshot.** `crates/er-npc-possess/src/config.rs:831`
  `GENERATION`, bumped at `:968` on every reload.
* **The renderer must not run the logic.** `picker/mod.rs:262-266` is explicit: the panel is drawn from a
  snapshot the game-thread tick republishes, and a frame that does not run the tick leaves the last
  snapshot on screen.

So the answer to "what single source of truth holds the live config" is: **the file is the source of truth,
`CONFIG`/`HotConfig` is the live cache, the game task is its only writer, and the overlay holds nothing.**

### Parity in both directions, and the two-writer reconciliation

**The lost-update problem is already solved in this crate and the solution is one line.**
`hotkeys.rs:182-184`, on `apply_enable_toggle`:

> It reloads before it flips for the same reason `apply_mark` does: a hand-edit made since the last poll
> must win, or a keypress would overwrite the player's file with a stale copy.

The sequence at `hotkeys.rs:190-198` is the contract every UI mutation must follow, unchanged:

```rust
let path = config_path();
let Ok(mut guard) = CONFIG.lock() else { return };
let hot = guard.get_or_insert_with(HotConfig::default);
let _ = hot.reload_if_changed(&path);   // the file wins over anything stale
let mut config = hot.current().clone(); // read-modify-write, inside the lock
config.<field> = <new value>;
match hot.save(&path, &config) { .. }   // write, re-read, re-parse, adopt into the watcher
```

`HotConfig::save` (`local_invasion_config.rs:800-813`) then does the other half: it adopts the text it just
wrote into the watcher, so **our own write is not re-reported as a user edit** -- which matters because a
spurious reload resets the key edge detectors (`local_invasion_config.rs:16-18`). And the watcher polls
**content, not mtime** (`local_invasion_config.rs:12-15`), so two saves inside one filesystem-mtime tick are
both seen and an external `touch` is not mistaken for an edit.

Answering the three acceptance criteria directly:

1. **Full coverage** -- yes, every key appears in the UI. Fifteen are live in both directions today; six are
   arm-only and are named above with what it would take to fix them; `named_locations` is not implemented in
   the mod at all and is shown read-only with its reason.
2. **Parity, and write-back** -- **recommendation: yes, always write back to the TOML, synchronously, on
   every change.** Not optional, not a "save" button. Three reasons, each decisive on its own: the hot-reload
   watcher will overwrite any memory-only value on its next poll; the file is what survives a restart, which
   is what the mark keys and the `enable_toggle_key` already promise; and a file and a UI disagreeing about
   what is in force is exactly the class of bug this repo keeps paying for. The UI opens showing
   `current_config_snapshot()`, which came from the file, so the open-state parity is free.
3. **Hot update from the file side** -- already in scope and already working. `refresh_config()`
   (`local_invasion_filter.rs:350`) is called from `current_config()` on every match and the tick reads the
   snapshot every frame, so an external edit lands within the ~1 s poll interval. The UI inherits it for
   free **provided it re-reads the snapshot every frame and caches nothing** -- the same discipline
   `picker::view()` imposes. No second watcher, no second copy.

### Plan

**Stage 1 -- the overlay, full read-out, and the settings that are already live. Viable now.**

1. Add to `crates/er-invasion-warp/Cargo.toml`, under `[target.'cfg(windows)'.dependencies]`, exactly what
   `crates/er-invasion-path/Cargo.toml` carries: `er-build-watermark-core`, `hudhook 0.9.2`
   (`default-features = false, features = ["dx12"]`), `windows 0.62` with `Win32_Foundation`,
   `Win32_System_LibraryLoader`, `Win32_System_SystemServices`.
2. New module `crates/er-invasion-warp/src/overlay.rs`, copied in shape from `er-invasion-path/src/render.rs`:
   one `fn draw(ui: &Ui)`, one `unsafe extern "C" fn guest_draw(frame: *const OverlayFrame)` that calls
   `adopt_frame` first, one `impl ImguiRenderLoop` that calls `draw` then `dispatch_guests(ui)` then
   `er_build_watermark_core::draw_rows(ui, log)`, and an installer that claims-or-registers. Invoke
   `er_build_watermark_core::export_overlay_host!()` in `lib.rs` or the crate becomes a host no guest can
   find.
3. Install it off the loader thread, not from `DllMain` -- hudhook's install takes locks that must not run
   under the loader lock (`overlay_host.rs:98-104`). The existing `spawn_catalog_task` thread in
   `crates/er-invasion-warp/src/lib.rs:155-343` is the natural place.
4. **Draw from a snapshot, never from the config lock.** Follow `picker::view()`: the game task publishes a
   `SettingsView` (an owned, already-formatted struct: every key's current value, plus the live tallies from
   `announce::tally()`, `measurement_tally()`, `tallies()` `:3078`) into a `Mutex<Option<SettingsView>>` once
   per tick, and the renderer clones it. Guard the whole draw with an `AtomicBool` "panel open" the way
   `is_drawing()` does, so a closed panel costs one relaxed load per `Present`.
5. **Mutations run on the game thread, never in `Present`.** The overlay records an intent -- a small
   `Mutex<Vec<SettingEdit>>` or one atomic per field -- and the next `tick()` drains it through the exact
   `apply_enable_toggle` sequence quoted above (reload, clone, mutate, `save`, log the round-trip result).
   This keeps `fs::write` off the render thread and makes the file-vs-UI reconciliation identical to the
   file-vs-keypress one that already works.
6. **Input, Stage 1: keyboard only.** er-invasion-warp already polls raw keys on the game thread
   (`hotkeys.rs`, `er-invasion-warp-core/src/keybind.rs`), so a panel navigated with bound keys -- the
   picker's exact model -- needs no new input plumbing and no ABI change. A new `settings_key` binding opens
   and closes it, parsed by the same `keybind::parse_key` every other key uses.
7. Gate the six arm-only keys behind a runtime check inside their handlers (see the coverage table) so the
   UI can honestly present them as two-way. If that is deferred, the UI must label them.
8. Update `scripts/me3-dll-conflicts.toml:645-684`: the `[compatible]` entry currently asserts this shell
   "installs no D3D12 Present or compositor hook", which the overlay makes false. Name the host/guest
   arbitration the way `er-invasion-path`'s entry does.
9. Gates: `cargo test -p er-invasion-warp -p er-invasion-warp-core`,
   `cargo fmt -p er-invasion-warp -- --check`, `python3 scripts/check-comment-caps.py <files touched>`,
   `python3 scripts/check-me3-dll-conflicts.py`, `python3 scripts/check-me3-shell-coverage.py`.

**Stage 2 -- mouse. Do not start it until Stage 1 has run live.** It needs one of: (a) er-invasion-warp
becomes the host, which is fragile because the host is whoever loads first; (b) `OverlayFrame` gains a
guest-side input request and `OVERLAY_ABI_TAG` is bumped from `0x0903`, with every existing guest rebuilt;
or (c) the panel moves into whichever shell hosts. And on top of the WndProc half, the DirectInput half:
`er-net-effects` blanks the left button while the pointer is over its own button
(`present_overlay.rs:308`, `input_suppression::set_pointer_over_overlay`), and without an equivalent every
click on the panel is also a swing. The measurement that decides it: in the user's real profile, does
er-invasion-warp host or guest? Read `is_confirmed_host()` / `guest_dispatches()` out of its telemetry.

**Do not remove the TOML.** It is the persistence layer, the hot-reload source, the reconciliation point
between the two writers, and the only surface carrying the ~180 lines of user documentation in
`DEFAULT_CONFIG_TOML`. The UI is a second front end onto it, not a replacement.

---

## Item 2 -- the banner is not always showing up

### Mechanism today (VERIFIED)

The banner is the game's own **auto-closing** announcement surface -- the one that says "Grace discovered".
`crates/er-invasion-warp/src/announce.rs:1-81` documents it: `CS::AnnounceMessage` carries text directly, and
`FeSystemAnnounceView` owns `systemAnnounceScrollBufferTimer` / `systemAnnounceScrollCount`, so **it times
itself out**.

* `install()` (`announce.rs:399`) detours `CS::FeSystemAnnounceView::Update` (`UPDATE_RVA = 0x8c_47c0`,
  `announce.rs:119`) purely to learn `LIVE_VIEW`. It is a bare `MhHook` rather than the union because
  argument 2 is a `float` in `xmm1` that the union dispatcher neither receives nor forwards
  (`announce.rs:364-382`).
* `show(text)` (`announce.rs:487-532`) leaks a NUL-terminated UTF-16 buffer, writes its pointer to
  `view+0xb10+0x08`, then `is_active = 1`, then `announcePlayState = 1`, in that order.
* **Nothing in this repo sets or extends how long it stays up.** There is no duration we write, no re-arm,
  and no clear. The engine's own state machine expires it.

One stale doc comment worth correcting while you are in the file: `announce.rs:88-118` says the 1.17 row
`0x8c47c0 -> 0x8c5960` has not landed and "the announcement banner is simply absent on 1.17". **The row
exists** -- `docs/recon/rva-map-1162-to-1170.verified.tsv:247`, `IDENTICAL-WHOLE`, ratio 1.000 -- and
`0x8c5960` is below the 1.17.1 carry boundary `0xafefe9` (`crates/er-game-base/src/game_build.rs:65`,
`:884`), so it needs no further shift on the installed build. The comment is now misleading, not wrong in a
way that breaks anything.

### Why the banner can be absent -- five independent causes, all VERIFIED

1. **Nothing is written unless the message is new.** `RejectNotice::observe` / `observe_success` /
   `observe_arrival` (`crates/er-invasion-warp-core/src/reject_notice.rs:134`, `:190`, `:230`) return `None`
   for a repeat of the same `(block, reason)` / success / arrival. `announce_verdict` carries a second,
   independent latch -- `LAST_VERDICT_BLOCK` at
   `crates/er-invasion-warp/src/local_invasion_filter/banner.rs:194`. Seamless retries roughly every 20 s
   (`reject_notice.rs:8`) and the same wrong place recurs, so silence is the *designed* steady state during a
   hunt. That design is defensible and it is also literally "the banner is not always showing up".
2. **`RejectNotice::reset()` is dead code.** `reject_notice.rs:266` says "Called when a search ends -- a new
   hunt is a new question". A whole-tree scan finds exactly one caller: its own unit test at `:517`. The
   "new hunt speaks up again" rule is documented and **not implemented**.
3. **`announce_rejection` only fires when the cancel actually lands** --
   `local_invasion_filter.rs:2030-2032`, inside `drive_pending_cancel`. When the cancel is declined the code
   deliberately shows nothing (`:2054-2061`). Open bd `er-effects-rs-9i0g` records `scan_for_session`
   returning owner `0x0` so cancels are declined; combined with `ersc_observers = false` (see the
   cross-cutting section) this is a live, common path to no banner.
4. **`LIVE_VIEW == 0` refuses silently after the first time.** `show` returns `false`
   (`announce.rs:489-494`) and `NOTICE_FAILED` (`local_invasion_filter.rs:261`) is a process-wide one-shot,
   so the log says so exactly once per launch.
5. **A placed notice can render blank.** `poll_measurement` (`announce.rs:226-271`) reads the game's own
   text measurement back; `oracle_invasion_warp_notices_empty`
   (`crates/er-invasion-warp-core/src/oracles.rs:198-207`) counts zero-width ones. Open bug
   bd `er-effects-rs-lp5f`. The single countdown slot means notices placed within three frames of each other
   are never weighed at all, so `shown - drawn` is **not** the blank count.

### Gap against the ask

| asked for | today | verdict |
|---|---|---|
| present during the whole time | engine-owned auto-close, a few seconds per message | **not achievable on this surface** |
| clear text after each | the engine clears it; we never do | no explicit clear exists |

Holding the game's banner open is not a tuning problem, it is the wrong surface. `Update` pops a queued
game message **only while `is_active` is false** (`announce.rs:22-32`) -- so pinning ours permanently would
suppress every real in-game announcement for the rest of the session, and re-writing `announcePlayState = 1`
each frame restarts the `Load` case and its measurement, which is also the thing `poll_measurement` reads.

**So item 2 is viable only by splitting the surface, and the persistent half is the item-1 overlay.** That
is the single reason to do them together:

* **Persistent line (overlay, mod-owned):** filter state, `mode`, hunt armed/stood down, last verdict,
  `RejectNotice::suppressed()` count, `announce::tally()` / `measurement_tally()`. Drawn every frame, so
  "present the whole time" is literally true and costs the game nothing.
* **Transient line (keep the game banner):** one auto-closing message per event, exactly as today. "Clear
  text after each" then becomes a real operation on the overlay's event slot: set text with a TTL, set it
  to empty when the TTL expires.

### Cheaper fixes that should land regardless (all viable today)

1. **Implement `RejectNotice::reset()`'s contract.** Call it where a hunt begins, beside the existing
   `INVASION_ACTUALLY_HAPPENED` clear at `local_invasion_filter.rs:1351-1354` (`state == abi.state_searching`),
   and in `stand_down_auto_search` (`:2140`). Reset `LAST_VERDICT_BLOCK` (`banner.rs:216`) in the same place --
   two latches, one lifetime, or they contradict each other.
2. **Make silence legible.** `RejectNotice::suppressed()` already counts the suppressed run and only reaches
   the log. Put it on the next banner that does speak ("... x7") or on the overlay line.
3. **Rate-limit `NOTICE_FAILED` instead of latching it once per process** (`local_invasion_filter.rs:261`,
   used at `banner.rs:68`, `:104`, `:162`, `:205`). A once-per-launch line cannot tell "the view was not up
   for the first rejection" from "the view was never up".
4. **Fix the stale 1.17 comment** at `announce.rs:88-118`.

---

## Item 3 -- a local invasion produces the banner later

### VERIFIED mechanism

Four announcement paths, and they are not symmetric:

| event | call site | when |
|---|---|---|
| filter off, destination arrives | `local_invasion_filter.rs:1772` `banner::announce_arrival` | **synchronous**, in the `SetMultiplayJoinData` detour |
| rejected / non-local | `local_invasion_filter.rs:1876` `banner::announce_verdict` | **synchronous**, same detour, same instant |
| cancel landed | `local_invasion_filter.rs:2031` `banner::announce_rejection` | deferred to `tick()` via `drive_pending_cancel`, up to `CANCEL_RETRY_TICKS = 600` ticks of retry (`:1962`) |
| **accepted / local** | `local_invasion_filter.rs:2837` `banner::announce_success` | deferred to `lobbyState == CLIENT` |

`Verdict::Keep` **deliberately announces nothing at judge time**. `local_invasion_filter.rs:1836-1841`:

> The banner for this does not fire here. A kept match is a match we allowed, not an invasion that
> happened: measured 2026-08-16, joins sat dead for 53-213s after this exact instant. Saying "Invasion
> successful" at join time can therefore be a lie.

It stores `PENDING_SUCCESS_BLOCK` (`:1425`, `:1841`) and the banner is emitted from `trace_join_progress`
only once the engine reports `LobbyState::Client` (`:2828-2839`).

**Root cause, stated plainly: it is not a poll interval, not a scan cadence, and not detection ordering. On
a local invasion there is no banner at judge time at all.** The first thing the player sees is "Invasion
successful: X", and the in-repo measurement of that delay is **0.57-3.5 s after join data**
(`local_invasion_filter.rs:1430-1433`), plus one `tick()` of granularity. A non-local match speaks in the
same frame the offer arrives. The asymmetry is the whole reported symptom.

### A second, worse failure on the same path -- HYPOTHESIS

`announce_success` is additionally gated by `INVASION_ACTUALLY_HAPPENED`
(`local_invasion_filter.rs:1434`, tested at `:2829` with `swap(true)`). That latch is cleared in exactly two
places: `trace_session_state` on a transition **into** `state_searching` (`:1351-1354`), and
`arm_self_recovery` (`crates/er-invasion-warp/src/local_invasion_filter/actions.rs:702`). **Both require a
resolvable Seamless session** -- `trace_session_state` is called from `tick()` at `:2568`, below the
`let Ok(session) = resolve_session() else { ... return }` early return at `:2560`.

With `ersc_observers = false` and no OSM, `resolve_session()` can fail for a whole run. If it does, the
latch is never cleared and **only the first local invasion of the session ever announces**.

*Measurement that settles it:* one session log with two successful local invasions. Look for two
`Invasion successful:` lines in `er-invasion-warp.log`; one line (or zero) with a `KEEP` for each confirms
the stuck latch. `oracle_invasion_warp_notices_shown` in `er-invasion-warp-telemetry.json` is the cheap
cross-check.

### Plan (viable, small)

1. **Add an immediate, honest line on `Verdict::Keep`** -- the exact mirror of `announce_verdict`'s
   `"Not local: {place}"` (`banner.rs:190-212`). Wording should state only what is certainly true at that
   instant, e.g. `"Local: {place}"` -- never an outcome. This preserves the 2026-09-04 / 2026-09-09 lesson
   recorded at `banner.rs:173-188` (the banner got this wrong in both directions once already) while
   removing the silence.
   * Give it its own `LAST_KEEP_BLOCK` latch in the shape of `LAST_VERDICT_BLOCK` (`banner.rs:216`).
   * **Do not route it through `RejectNotice`.** `Announced` has three variants (`reject_notice.rs:99-106`)
     and adding the keep as a fourth would make the later `Invasion successful` at the same block read as a
     repeat and be swallowed -- the exact bug `a_success_clears_the_rejection_latch_so_a_later_rejection_speaks`
     (`reject_notice.rs:622`) exists to prevent.
   * Keep the existing `"Invasion successful: {place}"` as the later confirmation. Two lines, two facts.
   * Respect the 40-character bound the test at `reject_notice.rs:531` enforces, and the 1728 px field the
     verdict banner's comment names (`banner.rs:199-201`).
2. **Clear `INVASION_ACTUALLY_HAPPENED` from a signal that does not need a Seamless session.** The join-data
   detour itself is the obvious one (`JOIN_DATA_AT_MS` is already stamped at `:1585`), or `lobbyState`
   returning to `NONE` in `trace_join_progress`, which runs above the `resolve_session` early return.
3. Do **not** move `announce_success` earlier. The 53-213 s dead-join measurement is why it is where it is.

---

## Item 4 -- the "No Invasions" message, repeatedly

### It is Seamless's message, not ours (VERIFIED)

No such string exists anywhere in this repo -- a regex sweep over `crates/`, `scripts/` and `docs/` for
`No invasion` / `no_invasion` / `No candidates` finds only prose about map pins. The message is:

`YKNX3_BREAKINFAILED`, id `0x6fff_43d9`, `crates/er-invasion-warp/src/local_invasion_filter/ersc.rs:286-297`:

> the notice a search that found nothing ends on, which the player reads as "Failed to invade session:"
> followed by "No sessions found" ... The wording is not in the module at all -- it comes from
> `SeamlessCoop/locale/english.json`, which the player owns and may edit -- so this id is the only stable
> handle on the message, and matching its text would be matching a file that is not ours.

### How each retry becomes a message (VERIFIED, from the recorded RE in `ersc.rs:261-282`)

1. `ersc+0x25a50` formats through `ersc+0x25020(repository, id, args)`, where
   `repository = [OSM + 0x50]` (`MOD_MESSAGE_REPOSITORY_OFFSET`, `ersc.rs:269`).
2. That formatter **returns null for an id the maps do not hold** (`ersc+0x250d8` is `xor edi,edi; ret`),
   and the caller tests the result at `ersc+0x25ac6` and jumps past the display call. Declining to format is
   Seamless's own way of not showing a message.
3. Otherwise it calls `[OSM + 0x88](0, 0, MenuString*, 0)` at `ersc+0x25b15`
   (`MESSAGE_DISPLAY_SEAM_OFFSET`, `ersc.rs:282`). The `MenuString` has "the same shape the game's own
   `GetGR_System_Message` fills in and hands to `showPopupMenu`".

Seamless retries roughly every 20 s (`reject_notice.rs:8`), so an empty bracket produces one of these per
retry. **No dedup exists anywhere.** The only two occurrences of the id in the tree are the constant itself
and one log line.

### What already exists toward a fix

`crates/er-invasion-warp/src/local_invasion_filter/menu_seams.rs:58-123` -- `report_menu_seams` -- prints who
owns `OSM+0x88` and ends with, literally, "the notice to refuse is id `0x6fff43d9` (YKNX3_BREAKINFAILED)".
It is already driven from the scan half of `resolve_session` with **no detour in `ersc.dll`**
(`menu_seams.rs:37-42`).

**It has never fired with a usable owner.** bd `er-effects-rs-2bd8`: run `br-20260910-171939-d923` resolved
the session with `owner 0x0`, so the report was skipped and `OSM+0x88` is still unowned. That bd issue names
this exact blocker: *"Blocks: hooking OSM+0x88 to refuse the notice."*

### Verdict: viable, but BLOCKED on one read-only measurement

The measurement, stated exactly: **make `report_menu_seams` fire with a real OSM, so the line naming
`show_message@+0x88`'s owner appears in `er-invasion-warp.log`.** It is a read, not a change to behaviour.
Two ways to get there that need no new reverse engineering:

* (i) Route `report_menu_seams` off `menu_object::capture_osm`
  (`crates/er-invasion-warp/src/local_invasion_filter/menu_object.rs:29-55`) as well as off the scan's owner.
  The lynchpin path already adopts a validated OSM at `lynchpin_use.rs:347-350`, and `capture_osm` logs it,
  so the pointer exists in runs where the scan's owner does not.
* (ii) bd `er-effects-rs-2bd8`'s inverse scan: walk `ersc.dll`'s writable sections for a qword `X` where
  `*(X + NEXT_OBJECT_OFFSET)` equals the session `differential_scan::narrow_to_changed` already proved
  (`crates/er-invasion-warp/src/local_invasion_filter/differential_scan.rs:116`).

### Three routes once the owner is known

**Route A -- recommended. Detour the GAME function at the far end of `OSM+0x88`, not ERSC.**
Whatever `+0x88` resolves to is a game address (Seamless pattern-scans for it and stores no absolute game
address in its image -- `ersc.rs:278-281`). er-invasion-warp already owns this exact pattern: the union detour
on `CS::CSMenuMan::OpenConversationChoicesMenu` in `lynchpin_use.rs:337-442`, which declines to build a
dialog when a scoped gate says so, and lets every other caller through untouched.

* Scope on the **caller**, not the text: accept only calls whose return address lies inside
  `[ersc_base, ersc_base + image_size)`. `ersc_module_base()` already exists
  (`local_invasion_filter.rs:2231`). This is content-free, so it survives a player editing
  `english.json` -- which is the rule `ersc.rs:294-296` sets and which any text match would break.
* Latch: show the **first** `BREAKINFAILED` of a hunt, suppress the rest until something changes. The
  natural reset edges are the same ones item 2 needs -- the transition into `state_searching`
  (`local_invasion_filter.rs:1351`) and `stand_down_auto_search` (`:2140`). One reset point, three latches
  (`RejectNotice`, `LAST_VERDICT_BLOCK`, this one), which is why item 2's fix 1 should land first.
* Register through `er_hook::register_union_hook`, never `MhHook::new` -- this crate's own manifest states
  the rule and bd `er-invasion-warp-plus-product-crashes-game-2026-09-02` is what happens when it is broken.

**Route B -- cheapest, but all-or-nothing.** Remove the `0x6fff_43d9` entry from the repository map at
`[OSM + 0x50]`, so `ersc+0x25020` returns null and **Seamless's own code** skips the display (step 2 above).
No detour anywhere; one write into Seamless's data. It suppresses the message *entirely* rather than after
the first, and it mutates a third-party module's state, which is a larger promise than the ask. Record it;
do not reach for it first.

**Route C -- only if A's owner turns out to be a shared game entry point.** If `+0x88` resolves to the game's
`showPopupMenu`/`GetGR_System_Message` path (the `MenuString` layout note at `ersc.rs:270-276` is a lead,
not an identification), the detour serves every popup in the game and the caller check from Route A becomes
mandatory rather than merely correct.

### What is NOT an option

Detouring `ersc+0x25a50`, `ersc+0x25020`, or anything else inside `ersc.dll`. See the cross-cutting section:
the `show` detour softlocks the game at +29.5 s and the `[compatible]` classification depends on
`ersc_observers = false`.

---

## Summary of verdicts

| # | verdict | blocked on |
|---|---|---|
| 1 | Viable. Full coverage is reachable: **15 of 21 keys are already live in both directions**, 6 are arm-only (`map_pins`, `steam_hooks`, the four `ersc_*`) and are fixed by gating inside the handlers rather than by uninstalling detours, and `named_locations` is unimplemented in the mod itself. Shared-state shape = `er-npc-possess`'s picker: the game task owns the config and republishes an owned snapshot, the render thread only clones it, and every mutation runs on the game thread through the existing reload-clone-mutate-`save` sequence. **Write back to the TOML on every change** -- the hot-reload watcher clobbers anything memory-only. Stage 2 (mouse) needs host status or an ABI bump. | nothing for Stage 1 |
| 2 | "Clear after each" viable now. **"Present the whole time" is not achievable on the game's auto-closing surface** and needs item 1's overlay. Four independent absence causes found, incl. `RejectNotice::reset()` being dead code. | nothing; item 1 for the persistent half |
| 3 | Root cause established statically: a local match announces **nothing** at judge time by explicit design; the first line is deferred 0.57-3.5 s to `lobbyState == CLIENT`. Fix is an immediate `Local: <place>` line at `Verdict::Keep` with its own latch. | nothing. One HYPOTHESIS (stuck `INVASION_ACTUALLY_HAPPENED`) needs a two-invasion session log |
| 4 | Identified: Seamless's `YKNX3_BREAKINFAILED` (`0x6fff_43d9`) through `[OSM+0x88]`, one per ~20 s retry, no dedup anywhere. Route A (game-side union detour + caller scope + latch) is viable. | **one read-only measurement**: make `report_menu_seams` fire with a real OSM so `+0x88`'s owner is named (bd `er-effects-rs-2bd8`) |

---

# Adversarial validation (2026-09-13)

A second agent was asked to REFUTE the document above, not to confirm it: static reading only, no
build, no launch, no runtime probe. Every line number cited above was re-opened. Nothing above has
been deleted or rewritten -- corrections are added here so the disagreement stays visible.

Headline: **three of the four verdicts move.** The key census overcounts the live keys by one; the
persistent-banner refusal is right about the mechanism but wrong about why, and the honest version
is more useful; and item 4 is **not blocked** -- the code path the plan proposes writing already
exists at `local_invasion_filter.rs:858`.

## Claim 1 -- the config census: 21 keys, "15 live" -- **REFUTED (off by one), 21 CONFIRMED**

The **21** is right, derived twice and independently: `LocalInvasionConfig` has exactly 21 `pub`
fields (`crates/er-invasion-warp-core/src/local_invasion.rs:234-392`), and
`parse_local_invasion_config_with_fallback` recognises exactly 21 key strings
(`local_invasion_config.rs:328-490`). Same 21 names, no alias, no hidden key. The live file on this
machine was re-read and matches the quoted block exactly.

**The liveness split is 14 / 1 / 6, not 15 / 1 / 6.** Count the document's own table (lines 151-165
above): 14 keys are marked live -- `enabled`, `mode`, `reject_notice`, `hunt`, `dll_users_only`,
`allowed_blocks`, `blocked_blocks`, `named_location_text_ids`, `mark_key`, `unmark_key`,
`enable_toggle_key`, `warp_nearest_key`, `warp_next_key`, `warp_other_area_key`. `named_locations`
is the 15th row and the table itself calls it **not implemented at all**. The prose at line 248
("Fifteen are live in both directions today") and the summary at line 567 ("15 of 21 keys are
already live") both fold the unimplemented key into the live count. **14 live, 1 unimplemented, 6
arm-only.** Correct the two summary lines before anyone sizes the work off them.

The classification itself survives attack. Each of the 14 was traced to its read site and each
re-reads through `current_config()` (`local_invasion_filter.rs:466`, which calls `refresh_config()`
first) or a snapshot taken that tick:

* `enabled` `:1769`, `mode` `:1816`, `reject_notice` `:1772`/`:1876` -- per match. Confirmed.
* `hunt` -- `lobby_publish.rs:1189-1202`, per query. Confirmed.
* `dll_users_only` -- `lobby_publish.rs:918-972`, compares `POOL_APPLIED` and re-advertises.
  Confirmed, and it is indeed the only key engineered for a live toggle.
* the three block/id lists -- through `judge`, and also per-pin through
  `map_pins_view.rs:27`/`:58`. Confirmed.
* the six key bindings -- re-read every poll (`hotkeys.rs:24`, `:39`, `:52`), and **both** edge
  detectors re-seat on a rebind: `MarkKeys::poll` at `hotkeys.rs:127-137` and
  `drive.rs:182-198` for the warp keys, whose actual read is `drive.rs:309`, not
  `local_invasion_filter.rs:111` (that line is a `pub use` re-export, not a read site).

`named_locations` "parsed and then ignored" -- **CONFIRMED** by a whole-workspace sweep: it is
written by the parser (`:414`), rendered (`:663`), counted in a log line
(`local_invasion_filter.rs:392`, `:414-420`) and consumed by nothing. `DEFAULT_CONFIG_TOML:155-163`
says so in the shipped file, exactly as quoted.

`lib.rs:214-218` -- **CONFIRMED verbatim**, including "a hook already installed stays installed".
`map_live_pins` ungated -- **CONFIRMED, and stronger than stated**: a regex sweep of the whole crate
finds `map_pins`, `current_config` and `config_snapshot` appearing in `lib.rs`, `lobby_publish.rs`,
`local_invasion_filter*.rs` and `drive.rs` **only**. `map_live_pins.rs`, `map_hooks.rs` and
`map_gfx.rs` contain zero config reads, so `restyle_live_pins` / `top_up_live_pins`
(`lib.rs:323-335`) are not gated by anything.

**New defect found in the Stage 1 plan, step 8.** The document says the `[compatible]` entry at
`scripts/me3-dll-conflicts.toml:645-684` "currently asserts this shell `installs no D3D12 Present or
compositor hook`". **It does not.** That entry (645-684) is entirely about the `0x140010043` crash,
the two removed `ersc.dll` detours and the 600s demotion run; the word "Present" does not occur in
it. The quoted sentence lives at **:702-703** and belongs to **`er-diag-harness`**. The underlying
advice (update the entry when an overlay lands) is still right; the stated basis is wrong, and an
implementer following it will hunt for a sentence that is not there. The shape to copy is
`er-invasion-path`'s entry at **:867-884**, which already names `er_overlay_register_guest_v1` and
"exactly one Present hook".

**Second new defect, and it bears directly on "write back on every change".**
`render_local_invasion_config` (`local_invasion_config.rs:605-607`) rebuilds the file by iterating
**`DEFAULT_CONFIG_TOML.lines()`** and substituting values. So every save discards anything the user
added by hand -- their own comments, their ordering, their blank lines -- and, because the shipped
default carries no `[local_invasion]` header while the parser explicitly supports the schema being
embedded in a **shared** TOML (`local_invasion_config.rs:314`, `SECTION_NAME`), a save into a shared
file would overwrite another crate's section wholesale. A keypress does this a few times a session
and nobody notices. A UI that writes on **every** change does it constantly. Either fix the writer
to edit in place, or say plainly in the UI that the file is regenerated.

## Claim 2 -- `hotkeys.rs:189-198` already solves two-writer reconciliation -- **PARTLY REFUTED**

The mechanical half is **CONFIRMED, exactly as cited**. `apply_enable_toggle` at `hotkeys.rs:189`
is the quoted reload/clone/mutate/`save` sequence, lines 190-198, character for character.
`HotConfig::save` at `local_invasion_config.rs:800-813` writes, re-reads, `adopt`s the text into the
watcher (`er-hotkey-config/src/reload.rs:105-107`), re-parses and returns whether the round trip
matched. The watcher is genuinely **content**-polled, not mtime: `HotFile::poll_with`
(`reload.rs:116-139`) compares the whole text, and `reload.rs:3-15` records why mtime was abandoned.
No overlay in the workspace does file I/O in `Present`.

**What is refuted is the atomicity, and the document leans on it twice.** `poll_with` returns `None`
when `now_ms < self.next_read_ms`, and the interval is `DEFAULT_POLL_INTERVAL_MS = 1000`
(`reload.rs:38`). So `hot.reload_if_changed(&path)` **does not read the file unless a second has
elapsed since the last read**. In this exact call path it essentially never does: `tick()` reaches
`current_config_snapshot()` at `local_invasion_filter.rs:2500` and `lib.rs:219` calls it again
before `tick()` even starts -- each one running `refresh_config()` -> `reload_if_changed` -> a read
that resets `next_read_ms` to now+1000 -- and `keys.poll()` (`:2529`) runs after both, in the same
frame. By the time `apply_enable_toggle` asks, the budget is spent and the call is a no-op.

The practical consequence is bounded but real: the in-memory config is **up to one second stale**,
so a hand-edit made inside that window is silently overwritten by the save. The comment's claim that
"a hand-edit made since the last poll must win" is the one thing that cannot be true -- it is the
*poll* that wins, and the poll is late. This is a read-modify-write with a ~1 s lost-update window,
not an atomic sequence. Call it that in the plan; it is still good enough for a human at a text
editor, and it is not good enough to be described as solved.

Two smaller holes in `save`, both from reading it line by line:

* A hand-edit landing **between** `fs::write` (`:806`) and `read_to_string` (`:807`) is adopted and
  parsed, so the user's edit wins -- but `matched` then comes back `false` and the caller logs
  *"WROTE the config but it did not read back identically -- this is a bug in the config writer,
  not in your file"* (`hotkeys.rs:208-211`). That is a **misdiagnosis** shipped to the user.
* The file being deleted in that same window makes `read_to_string` fail, so `save` returns `Err`
  and the caller says *"the filter is UNCHANGED"* -- after the write already happened.

Neither is fatal. Both get more likely the moment a UI starts saving on every keystroke, which is
what the plan recommends.

## Claim 3 -- copy `er-npc-possess`'s picker; no overlay edits config -- **CONFIRMED**

Every cite checks out: `picker/mod.rs:271` `tick(settings, pad_buttons)` republishes the snapshot,
`:243` `view()` hands back a `clone()`, `:249` `is_drawing()` is the relaxed `AtomicBool`,
`:262-266` is the explicit "drawn from a snapshot the tick republishes" comment, `render.rs:57`
`draw(ui)` does nothing else, `config.rs:831` `GENERATION` bumped at `:968`. Every
`er_build_watermark_core::overlay_host` cite is exact (`:70-82`, `:90`, `:94`, `:121`, `:126`,
`:132`, `:161`, `:186`, `:229`, `:268`, `:287`, `:322`), including `:98-104` for the loader-lock
rule. `er-invasion-path/src/render.rs` `:155` / `:168` / `~:233` are right.

"No existing overlay edits config" -- **CONFIRMED** by a regex sweep for `.save(`, `fs::write`,
`HotConfig` and `persist` across all six overlay modules in the workspace
(`er-npc-possess/overlay.rs`, `.../picker/render.rs`, `er-net-effects/present_overlay.rs`,
`er-invasion-path/render.rs`, `er-build-watermark-core/overlay.rs`,
`er-quickload/experiments/present_overlay.rs`): zero hits. No precedent was missed.

## Claim 4 -- "present the whole time" is not achievable -- **CONFIRMED as to consequence, REFUTED as to reason**

I re-derived this from the 1.16.2 Ghidra dump rather than trusting the module comment, and the
mechanism is now settled independently of `announce.rs`.

`FeSystemAnnounceView::Update` (`0x1408c47c0`), decompiled:

```c
if ((param_1->field1949_0xb10).is_active == false) {
    if ((param_1->systemAnnounceViewModel->messageQueue).size != 0) {
        pAVar2 = FUN_140841b00(param_1->systemAnnounceViewModel);   // queue.pop
        FUN_1408c4710(&param_1->field1949_0xb10, pAVar2);           // msg = *popped
        param_1->announcePlayState = Load;
    }
    bVar4 = (param_1->field1949_0xb10).is_active == false;
}
if (!bVar4) { FUN_1408c48c0(param_1, param_2); }                    // display step
```

So **pinning `is_active` withholds every game announcement** -- VERIFIED, not inferred. The refinement
is that they are not discarded: they accumulate in `messageQueue`, whose push
(`PushMessageForDisplay`) bounds it at `size + 1 < 10`, so roughly the first nine are delayed
indefinitely and everything after is refused. "Suppress every real in-game announcement for the rest
of the session" is right in effect.

**But the document's stated reason -- that the duration is engine-owned and unreachable -- is wrong,
and the correct reason is more useful.** The display step `FUN_1408c48c0` decompiles to a 13-state
machine: `Load -> FadeIn -> ScrollReset -> BufferWait -> Scrolling -> PostScrollBuffer ->
PostScrollBufferWait -> RepeatCheck -> HidePlaying -> FadeOut -> Dequeue`, and only `case Dequeue`
sets `is_active = false`. Its lifetime is **parameterised, not fixed**: `Load` seeds
`systemAnnounceScrollCount` from `MenuCommonParam->systemAnnounceScrollCount` (or `10` when the
param row is missing, `1` when the text does not overflow), and `RepeatCheck` decrements it and
jumps back to `ScrollReset` while it is positive. Writing a large `systemAnnounceScrollCount` --
one field on the view -- would keep the line cycling for as long as you like.

So the honest refusal is not *"we cannot hold it open"*. It is: **holding it open is one field write,
and the price is the game's own announcement queue -- roughly nine messages delayed, the rest
dropped.** That is a trade worth stating to the user rather than a capability worth denying. The
conclusion (split the surface; put the persistent line on the item-1 overlay) is unchanged and
correct; the argument for it should be the queue, not the timer.

Everything else in item 2 verified: `show` at `announce.rs:487-532` writes text pointer, then
`is_active`, then `announcePlayState`, and writes **no** timer -- confirmed. `install()` `:399`,
`UPDATE_RVA = 0x8c_47c0` `:119`, `LIVE_VIEW == 0` refusal `:488-494`, `poll_measurement` `:226-271`,
`banner.rs:194` `LAST_VERDICT_BLOCK` / `:216` its definition / `:199-201` the 1728 px note -- all
exact. The stale-comment finding is **correct and worth landing**: `rva-map-1162-to-1170.verified.tsv`
line **247** carries `0x1408c47c0 -> 0x1408c5960`, `IDENTICAL-WHOLE`, ratio 1.000, and `0x8c5960 <
CARRY_1171_BOUNDARY_RVA = 0xafefe9` (`game_build.rs:65`, applied at `:883-889`), so the row has
landed and needs no 1.17.1 carry.

One stale citation: bd `er-effects-rs-lp5f` is still open, but its described cause (the partial
`DLString::assign` write) was superseded -- `announce.rs` now writes the raw `wchar_t*` at
`AnnounceMessage+0x08`, which the `Load` case prefers, and the module docs record that fix. Blank
banners remain possible; that issue is no longer evidence of why.

## Claim 5 -- `RejectNotice::reset()` is dead code -- **CONFIRMED**

`reset()` is defined at `reject_notice.rs:266`. A python sweep of every `.rs` file in the workspace
(rtk not used, for the redaction reason) finds exactly one call: `notice.reset()` at `:517`, inside
`#[cfg(test)] mod tests`, which opens at `:273`. The live instance is the `REJECT_NOTICE` static at
`local_invasion_filter.rs:257-258` and nothing calls `reset` on it. No macro-generated caller
exists. The ~20 s retry cadence is `reject_notice.rs:8`, as cited.

## Claim 6 -- local-invasion banner lateness -- **CONFIRMED**

`Verdict::Keep` announcing nothing is real and deliberate: `local_invasion_filter.rs:1836-1841`, the
quoted comment, followed by `PENDING_SUCCESS_BLOCK.store(...)` and no banner call. The success line
is emitted only at `:2828-2839`, gated on `lobby_state == CLIENT` and on
`INVASION_ACTUALLY_HAPPENED.swap(true)`. The non-local paths at `:1772` and `:1876` are synchronous
inside the detour. The asymmetry is exactly as described.

**The 0.57-3.5 s figure is a recorded measurement, not a guess** -- `local_invasion_filter.rs:1430-1433`:
"Measured 2026-08-17: every real join reached it 0.57-3.5s after join data, and not one of the
eleven rejected matches ever did". It is a source comment rather than a `bd` memory or an artifact,
so it is one author's recorded run, not independently reproducible from this tree -- but it is a
stated measurement with a date and an n, which is what the claim needs.

The HYPOTHESIS also survives: `trace_session_state` (`:1340`), the only non-recovery clearer of
`INVASION_ACTUALLY_HAPPENED` (`:1351-1354`), is called at `:2568`, below the
`let Ok(session) = resolve_session() else { ... return }` at `:2560`. So with no resolvable session
the latch never clears. Still needs the two-invasion log.

## Claim 7 -- "No Invasions" is Seamless's, and the fix is BLOCKED -- **identification CONFIRMED, BLOCKED verdict REFUTED**

The identification is solid. `YKNX3_BREAKIN_FAILED_MESSAGE_ID = 0x6fff_43d9` at `ersc.rs:297`,
`MESSAGE_DISPLAY_SEAM_OFFSET = 0x88` at `:282`, `MOD_MESSAGE_REPOSITORY_OFFSET = 0x50` at `:269`,
all with the recorded RE at `:261-296`. A whole-tree sweep confirms the id occurs exactly twice:
the constant, and the log line at `menu_seams.rs:115-122` which ends literally with "the notice to
refuse is id {:#x} (YKNX3_BREAKINFAILED)". No dedup exists anywhere.

**The blocker does not hold.** `report_menu_seams` has **three** call sites, not one:

| site | driven by | gate |
|---|---|---|
| `local_invasion_filter.rs:694` | the ERSC `show` observer | dead while `ersc_observers = false` |
| `local_invasion_filter.rs:837` | the scan half | `owner != 0` -- this is the one that failed in bd `er-effects-rs-2bd8` |
| **`local_invasion_filter.rs:858`** | **the OSM a detour handed over** | none; `:854-855` says "This half always has an owner -- `osm` is what a detour handed over" |

That third site is fed by `menu_object::adopt_menu_object` (`menu_object.rs:57`), which the
**game-side** `OpenConversationChoicesMenu` union detour calls at `lynchpin_use.rs:347-350`. That
detour is not disarmed by `ersc_observers = false`. So the plan's proposed option (i) -- "route
`report_menu_seams` off `menu_object::capture_osm` as well as off the scan's owner" -- **is already
implemented**. What has not happened is a run in which the lynchpin menu was opened so the path
executed. Under the 2026-07-22 standing order the agent drives that input itself, so this is a test
to run, not a mechanism to build, and it is not a blocker.

**Route B is also mis-classified, and the misclassification is what produced the BLOCKED verdict.**
The plan calls it "all-or-nothing ... suppresses the message entirely". It is not: the document's own
step 2 establishes that `ersc+0x25020` returns null for an id the maps do not hold and the caller at
`ersc+0x25ac6` jumps past the display. Removing an entry is reversible, so *remove after the first
message, restore at the hunt-reset edge* is a complete dedup -- exactly what item 4 asks for -- and
it never needs `+0x88`'s owner at all. Its real cost is understanding the repository's container
layout, which is honest RE work and should be priced as that, not dismissed as all-or-nothing.

**And the static route was never attempted, which AGENTS.md says should come first.** The sibling
seams are already named game functions in the 1.16.2 dump, checked just now:
`+0xa8 = 0x140e9e4f0 = OpenConversationChoicesMenu` -- *the function this crate already detours* --
and `+0xb0 = FUN_140800950` / `+0xb8 = FUN_140800840`, both reached from
`FUN_1407c2ab0(CSPlayerMenuCtrl*)`, the multiplayer menu builder. `+0x88`'s owner is almost certainly
in that same neighbourhood and nameable from the dump's call graph. I did **not** identify it: the
obvious candidates do not match ersc's `(0, 0, MenuString*, 0)` arity -- `showPopupMenu(MenuString*)`
`0x1405f5e80` takes one argument, `CS::CSPopupMenu::ShowMenu(CSPopupMenu*, MenuString*)`
`0x1407ee510` two, `GetGR_System_Message(MenuString*, int)` `0x140762d50` two. So Route C's premise
is unconfirmed. But "not yet identified statically" is a different verdict from "blocked on a
runtime measurement", and only the first one is supported.

**Corrected verdict for item 4: viable, not blocked. Cheapest order is (1) exercise the existing
`:858` path by opening the lynchpin menu, (2) static identification of `+0x88` from the dump's
menu-function neighbourhood, (3) Route A's detour with the caller-scope check.**

## Claim 8 -- detouring `ersc.dll` is off the table -- **CONFIRMED for the site, REFUTED as a general rule**

bd `detouring-ersc-show-reproduces-the-0x10043-crash-at-295s-2026-09-09` is real and says what the
plan says: the `show` detour at **`ersc+0x241a0`** faulted at `game+0x11f42` at +29.5 s, then 215
more with `rsp` marching down, i.e. a load softlock. The `me3-dll-conflicts.toml:659-665` entry adds
the mechanism -- MinHook registers no unwind info and `ersc.dll` is Themida-virtualised.

That is **one address**, and the tree records a counter-example: the `ersc_invade_observer` doc on
`LocalInvasionConfig` states that "a Frida `Interceptor` sat on this exact address for that entire
run -- dozens of invades, no crash -- which says an inline hook here is survivable." So "detouring
`ersc.dll` is off the table" overstates the evidence; "the `show` seam is measured fatal and the
crate defaults every ERSC detour off" is what is proven. The practical posture is unchanged and
correct -- do not reach for an ERSC detour here -- but do not cite it as a law.

The second cited memory (`invasion-warp-cancel-dies-cpp-throw-crosses-extern-system-2026-09-08`) is
about a C++ throw crossing a nounwind ABI in the ERSC **cancel action**. That is an ABI defect, not
evidence about detours, and listing it under "do not detour ersc" conflates two different failures.

## Claim 9 -- mouse is Stage 2 because `MessageFilter` is host-only -- **PARTLY REFUTED**

The premise is right: `message_filter` is a method on `ImguiRenderLoop`
(`er-net-effects/src/present_overlay.rs:239-244`), so only the host can filter WndProc messages, and
`OverlayFrame` (`overlay_host.rs:70-82`) carries nothing about input.

**The conclusion does not follow, because a guest already takes mouse clicks in this workspace
today.** `er-net-effects`'s click handling lives in `draw_bar` (`present_overlay.rs:290-308`):

```rust
let hovered = rect_contains(toggle, ui.io().mouse_pos);
if hovered && ui.is_mouse_clicked(MouseButton::Left) { ... }
input_suppression::set_pointer_over_overlay(hovered);
```

and `draw_bar`'s own doc at `:247-249` says it is "the only drawing path, taken identically whether
this module hosts the imgui context or is a guest inside another module's render loop". `adopt_frame`
installs the host's context, so `ui.io().mouse_pos` and `is_mouse_clicked` work for a guest. The
host-only `message_filter` closes what its own comment at `:235-238` calls "the legacy-message half"
-- and states that half is **not** the one that stops a click becoming an attack.

**The real obstacle is different and smaller than an ABI bump:** the load-bearing half, the
DirectInput blanking, is `er_net_effects::input_suppression`, declared `mod input_suppression;`
(private) at `er-net-effects/src/lib.rs:25` with `set_pointer_over_overlay` as `pub(crate)`
(`input_suppression.rs:125`). er-invasion-warp cannot call it. So mouse input needs that module
**extracted into a shared crate** the way `overlay_host` already was -- plus `er-net-effects`
actually being in the profile to own the DirectInput union hook. That is a refactor, not
`OVERLAY_ABI_TAG` `0x0903 -> 0x0904` and not host status. Stage 2 may still be the right sequencing;
the reason given for it is not.

## Defects found, collected

1. **Live-key count is 14, not 15** (lines 248 and 567). `named_locations` is counted as live in the
   prose while the table calls it unimplemented.
2. **`me3-dll-conflicts.toml:645-684` does not contain the quoted sentence** (Stage 1, step 8). It is
   at `:702-703`, in `er-diag-harness`. Copy `er-invasion-path`'s entry at `:867-884` instead.
3. **`reload_if_changed` is throttled to 1000 ms and is a no-op in this call path**, so the
   reload/mutate/save sequence is a bounded-stale read-modify-write, not the atomic reconciliation
   the document presents. Two of the three acceptance answers rest on it.
4. **`render_local_invasion_config` regenerates the file from `DEFAULT_CONFIG_TOML`**, discarding
   user-added content and, in a shared TOML, another crate's section. "Write back on every change"
   multiplies that.
5. **Item 4 is not blocked.** `report_menu_seams` already fires from a detour-supplied OSM at
   `local_invasion_filter.rs:858`; the plan proposes building what exists. Route B is reversible and
   therefore a dedup, not an all-or-nothing suppression. The static identification of `+0x88` was
   never attempted, and AGENTS.md puts it ahead of a runtime probe.
6. **"Present the whole time" is refused for the wrong reason.** The lifetime is parameterised
   (`systemAnnounceScrollCount`, seeded at `Load`, decremented at `RepeatCheck`); the real price is
   the game's announcement queue, which is bounded at ~9 and then drops.
7. **"Detouring ersc.dll is off the table" overstates one measured site**, and the C++-throw memory
   is an ABI failure, not detour evidence.
8. **Mouse input is not gated on host status.** `er-net-effects` already clicks from its guest path;
   the gap is that `input_suppression` is a private module of another crate.
9. Minor citation drift: `warp_keys_in_force` is read at `drive.rs:309`, not
   `local_invasion_filter.rs:111` (a `pub use`); bd `er-effects-rs-lp5f`'s stated cause is
   superseded by the current `announce.rs`.

Everything not listed above was checked and found accurate, including every `overlay_host`,
`picker`, `announce`, `banner`, `ersc` and `menu_seams` line number, the live TOML quote, and the
21-key total.
