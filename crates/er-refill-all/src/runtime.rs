//! The windows-only half: the gate hook, the pad/keyboard read, and the native writes.
//!
//! # The gate is structural, not a check
//!
//! The requirement is that the hotkey "can only have an effect when the user is in the menu view
//! that would have any refillable item options". That menu is the storage box, and its dialog is
//! `CS::DepositoryDialog`. Rather than poll "is the storage box open?" from somewhere else -- a
//! question that can be asked wrongly, go stale by a frame, or answer true for a dialog that is
//! constructed but not yet showing -- this DLL runs FROM INSIDE that dialog's own per-frame update.
//! With any other menu open, or none, the code below is simply never called. There is no check to
//! get wrong.
//!
//! Getting there took two facts:
//!
//! * A `MenuWindow`'s update is **vtable slot 2**, signature `(this, f32 delta, InputData*)` --
//!   established previously for `TitleTopDialog` (bd `HOOK-DESIGN-titletopdialog-update-...`).
//!   `DepositoryDialog`'s slot 2 is a bare `JMP` thunk at `0x1408d6e10` into the shared
//!   `0x140745570`, so the dialog does not override the update; it inherits it.
//! * The thunk is 5 bytes -- exactly a `JMP rel32` -- which is too tight to hook without
//!   relocating into whatever follows it. So the hook goes on the shared function and identifies
//!   the caller by its vtable instead.
//!
//! **And the vtable IS an identity here, which was checked rather than assumed.** `0x142aebba0`
//! has exactly two references in the whole image, both inside `DepositoryDialog::DepositoryDialog`
//! (the `LEA` pair that assigns `vfptr`). No other class installs it. That matters because a
//! vtable shared between classes would latch this feature onto menus that have no storage box in
//! them -- see bd `shared-vtable-is-not-an-identity-verify-uniqueness-before-latching`.

use std::sync::atomic::{AtomicU16, AtomicU64, AtomicUsize, Ordering};

use er_game_base::{
    mem::{game_module_base, safe_read_usize},
    rva::{GAME_DATA_MAN_GLOBAL_RVA, REPLANISH_ITEMS_FROM_CHEST_RVA, SHOULD_REPLENISH_ITEM_RVA},
};

use crate::{
    config,
    log::refill_log,
    mark::{INSERT_CEILING, MarkOutcome, next_target_state},
    pad::{PadEdge, pad_chord_name},
};

/// `CS::ItemReplenishStateTracker::SetState(tracker*, int* itemId, bool state)`. The absolute
/// setter, and the only function here that writes a NEW entry.
const SET_STATE_RVA: usize = 0x23dd80;

/// `GameDataMan + 0x8` -> `mainPlayerGameData`.
const GAME_DATA_MAN_MAIN_PLAYER_GAME_DATA_OFFSET: usize = 0x8;
/// `PlayerGameData + 0x2b0` (`equipGameData`, by value) `+ 0x338` (`itemReplenishStateTracker*`).
const PLAYER_GAME_DATA_ITEM_REPLENISH_TRACKER_OFFSET: usize = 0x5e8;

/// `FUN_140745570` -- the shared `MenuWindow` update that `DepositoryDialog`'s vtable slot 2
/// thunks straight into. `(MenuWindow* this, f32 delta, InputData* input)`.
const MENU_WINDOW_UPDATE_RVA: usize = 0x745570;
/// `FUN_1408f13b0(DepositoryDialog*, int)` -- REBUILD THE DISPLAYED ITEM LIST.
///
/// Writing replenish state changes nothing on screen by itself. The vanilla toggle
/// (`FUN_1408d87d0`) calls `SetItemReplenishState` and then IMMEDIATELY calls this with `1`; it
/// tail-calls `FUN_1408f6dc0`, which resets the dialog's gaitem list and re-runs the row-build
/// lambda through vtable slots `0xf`/`0x10`. Without it the rows keep whatever refill icon they
/// were built with, so the tracker and the screen disagree -- which is indistinguishable, from the
/// player's side, from the hotkey having done nothing at all. Measured 2026-08-25: a run wrote all
/// 449 entries and the icons did not move.
const DEPOSITORY_DIALOG_REFRESH_RVA: usize = 0x8f13b0;

/// `CS::DepositoryDialog::vftable`. Assigned ONLY by that class's constructor (verified: two
/// references in the image, both in `DepositoryDialog::DepositoryDialog`), so `*this == this`
/// is a sound identity test for "the storage box dialog is the one updating".
const DEPOSITORY_DIALOG_VFTABLE_RVA: usize = 0x2aebba0;

/// `ItemReplenishStateTracker.count`, a `longlong` after `ItemReplenishStateEntry[2048]`.
const TRACKER_COUNT_OFFSET: usize = 0x4008;
/// `ItemReplenishStateEntry { i32 ItemId; bool autoReplenish; }`, padded to 8.
const TRACKER_ENTRY_SIZE: usize = 8;
const TRACKER_ENTRY_AUTO_REPLENISH_OFFSET: usize = 4;

/// Goods item ids are `0x40000000 | rowId`; weapon ids are the row id unchanged. These are the
/// only two high nibbles `GetEquipParamReplenishType` accepts.
const GOODS_ITEM_ID_FLAG: u32 = 0x4000_0000;

static GAME_BASE: AtomicUsize = AtomicUsize::new(0);
static ORIG_MENU_WINDOW_UPDATE: AtomicUsize = AtomicUsize::new(0);
static HOOK_INSTALLED: AtomicUsize = AtomicUsize::new(0);

/// Depository update frames seen, i.e. frames the storage box was actually open.
static DEPOSITORY_FRAMES: AtomicU64 = AtomicU64::new(0);
static CYCLES_RUN: AtomicU64 = AtomicU64::new(0);
/// Last pad sample, so the edge detector and the rebind seed share one read per frame.
static LAST_PAD_BUTTONS: AtomicU16 = AtomicU16::new(0);

type MenuWindowUpdateFn = unsafe extern "system" fn(*mut core::ffi::c_void, f32, *mut u8);
type SetStateFn = unsafe extern "system" fn(usize, *mut i32, bool);
type ShouldReplenishItemFn = unsafe extern "system" fn(usize, *mut i32) -> bool;
type ReplanishItemsFromChestFn = unsafe extern "system" fn();
type DepositoryRefreshFn = unsafe extern "system" fn(*mut core::ffi::c_void, u32);

/// Per-frame state that only the game's menu thread touches.
///
/// Not behind a mutex: the update hook is called from the one thread that runs menu updates, and a
/// lock on a per-frame path would be paid every frame to protect against a second caller that does
/// not exist. Reached through a `static mut`-style cell rather than a `Mutex` for that reason.
struct Edges {
    pad: PadEdge,
    keyboard_was_down: bool,
}

static mut EDGES: Option<Edges> = None;

pub(crate) fn install(base: usize) {
    use std::ffi::c_void;

    use er_hook::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook};

    if HOOK_INSTALLED.swap(1, Ordering::SeqCst) == 1 {
        return;
    }
    GAME_BASE.store(base, Ordering::SeqCst);

    match unsafe { MH_Initialize() } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => {
            refill_log(format_args!("install: MH_Initialize failed: {status:?}"));
            HOOK_INSTALLED.store(0, Ordering::SeqCst);
            return;
        }
    }

    let target = base + MENU_WINDOW_UPDATE_RVA;
    let hook = match unsafe {
        MhHook::new(
            target as *mut c_void,
            menu_window_update_hook as *mut c_void,
        )
    } {
        Ok(hook) => hook,
        Err(status) => {
            refill_log(format_args!(
                "install: MhHook::new(MenuWindow::Update @0x{target:x}) failed: {status:?}"
            ));
            HOOK_INSTALLED.store(0, Ordering::SeqCst);
            return;
        }
    };
    ORIG_MENU_WINDOW_UPDATE.store(hook.trampoline() as usize, Ordering::SeqCst);
    // Not optional: MH_ApplyQueued applies the QUEUE, so a hook that was created but never queued
    // leaves the detour installed-but-disabled -- the gate never fires and the feature is silently
    // inert, which reads exactly like the hotkey not being detected.
    if let Err(status) = unsafe { hook.queue_enable() } {
        refill_log(format_args!(
            "install: queue_enable(MenuWindow::Update @0x{target:x}) failed: {status:?}"
        ));
        HOOK_INSTALLED.store(0, Ordering::SeqCst);
        return;
    }

    match unsafe { MH_ApplyQueued() } {
        MH_STATUS::MH_OK => {
            let config = config::init_config();
            unsafe {
                EDGES = Some(Edges {
                    pad: PadEdge::new(config.pad()),
                    keyboard_was_down: false,
                });
            }
            refill_log(format_args!(
                "install: hooked MenuWindow::Update @0x{target:x}; gate = DepositoryDialog vftable \
                 @0x{:x}; gamepad_hotkey={} hotkey={} refill_immediately={} config={}",
                base + DEPOSITORY_DIALOG_VFTABLE_RVA,
                pad_chord_name(config.pad()),
                config
                    .keyboard()
                    .map_or_else(|| "(none)".to_owned(), er_hotkey_config::chord_name),
                config.refill_immediately,
                config.config_path.display(),
            ));
        }
        status => {
            refill_log(format_args!("install: MH_ApplyQueued failed: {status:?}"));
            HOOK_INSTALLED.store(0, Ordering::SeqCst);
        }
    }
}

/// The hook. Runs for EVERY `MenuWindow`, and returns immediately for all but one.
///
/// The original is called first, unconditionally, so that a fault anywhere in our code cannot cost
/// the player the menu frame itself.
unsafe extern "system" fn menu_window_update_hook(
    this: *mut core::ffi::c_void,
    delta: f32,
    input: *mut u8,
) {
    let original = ORIG_MENU_WINDOW_UPDATE.load(Ordering::SeqCst);
    if original != 0 {
        let original: MenuWindowUpdateFn = unsafe { std::mem::transmute(original) };
        unsafe { original(this, delta, input) };
    }

    let base = GAME_BASE.load(Ordering::SeqCst);
    if base == 0 || this.is_null() {
        return;
    }
    // THE GATE. Any menu window that is not the storage box leaves here.
    let vfptr = unsafe { (this as *const usize).read() };
    if vfptr != base + DEPOSITORY_DIALOG_VFTABLE_RVA {
        return;
    }
    DEPOSITORY_FRAMES.fetch_add(1, Ordering::Relaxed);

    // Sample the pad BEFORE the config reload, so a rebind can seed its latch from the buttons
    // that are down at that instant instead of clearing it and manufacturing a press.
    let buttons = read_pad_buttons();
    LAST_PAD_BUTTONS.store(buttons, Ordering::Relaxed);

    if let Some(update) = config::poll_reload() {
        config::log_update(&update);
    }
    let config = config::config();

    // Safety: the menu update runs on one thread, and this is the only code that touches EDGES.
    let edges = unsafe { (&raw mut EDGES).as_mut() }.and_then(Option::as_mut);
    let Some(edges) = edges else { return };

    if edges.pad.rebind(config.pad(), buttons) {
        refill_log(format_args!(
            "hotkey: gamepad binding now {}",
            pad_chord_name(config.pad())
        ));
    }

    let pad_pressed = edges.pad.feed(buttons);
    let keyboard_pressed = match config.keyboard() {
        Some(chord) => keyboard_edge(chord, &mut edges.keyboard_was_down),
        None => {
            edges.keyboard_was_down = false;
            false
        }
    };

    if pad_pressed || keyboard_pressed {
        let source = if pad_pressed { "gamepad" } else { "keyboard" };
        unsafe { run_cycle(base, this, source, config.refill_immediately) };
    }
}

/// Read controller 0's `wButtons`. Zero when no pad is connected or XInput is absent.
#[cfg(windows)]
fn read_pad_buttons() -> u16 {
    use windows::{
        Win32::System::LibraryLoader::{GetModuleHandleA, GetProcAddress},
        core::PCSTR,
    };

    /// Resolved `XInputGetState`, or `PROC_ABSENT` once we know there is none.
    static XINPUT_GET_STATE: AtomicUsize = AtomicUsize::new(0);
    const PROC_ABSENT: usize = usize::MAX;

    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    struct XInputGamepadRaw {
        buttons: u16,
        left_trigger: u8,
        right_trigger: u8,
        thumb_lx: i16,
        thumb_ly: i16,
        thumb_rx: i16,
        thumb_ry: i16,
    }
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    struct XInputStateRaw {
        packet: u32,
        gamepad: XInputGamepadRaw,
    }
    type XInputGetStateFn = unsafe extern "system" fn(u32, *mut XInputStateRaw) -> u32;

    let cached = XINPUT_GET_STATE.load(Ordering::SeqCst);
    if cached == PROC_ABSENT {
        return 0;
    }
    let proc: XInputGetStateFn = if cached == 0 {
        // The game loads XInput for its own gamepad support, so this resolves without a
        // LoadLibrary. If it is absent the session is keyboard-only and the pad binding is simply
        // unavailable -- not an error worth logging every frame.
        let mut found = 0usize;
        for dll in [c"xinput1_4.dll", c"xinput1_3.dll", c"xinput9_1_0.dll"] {
            let Ok(module) = (unsafe { GetModuleHandleA(PCSTR(dll.as_ptr().cast::<u8>())) }) else {
                continue;
            };
            if let Some(address) =
                unsafe { GetProcAddress(module, PCSTR(c"XInputGetState".as_ptr().cast::<u8>())) }
            {
                found = address as usize;
                break;
            }
        }
        if found == 0 {
            XINPUT_GET_STATE.store(PROC_ABSENT, Ordering::SeqCst);
            return 0;
        }
        XINPUT_GET_STATE.store(found, Ordering::SeqCst);
        unsafe { std::mem::transmute::<usize, XInputGetStateFn>(found) }
    } else {
        unsafe { std::mem::transmute::<usize, XInputGetStateFn>(cached) }
    };

    let mut state = XInputStateRaw::default();
    // ERROR_SUCCESS(0) == connected. Any other result means no pad in slot 0.
    if unsafe { proc(0, &raw mut state) } == 0 {
        state.gamepad.buttons
    } else {
        0
    }
}

#[cfg(not(windows))]
const fn read_pad_buttons() -> u16 {
    0
}

/// Keyboard edge for the optional chord.
///
/// Both bits of `GetAsyncKeyState` are used and the low one is not optional: it means "pressed
/// since the previous call ON THIS THREAD", so it catches a press that happened and was released
/// between two frames. This runs on the game's menu thread, which is the only place the call is
/// reliable under Wine/Proton -- a dedicated poll thread measured 1089 polls for 5 observed
/// key-downs.
#[cfg(windows)]
fn keyboard_edge(chord: er_hotkey_config::keys::Chord, was_down: &mut bool) -> bool {
    use er_hotkey_config::keys::{MODIFIER_CTRL, MODIFIER_SHIFT};
    use windows::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState;

    const HELD: u16 = 0x8000;
    const PRESSED_SINCE_LAST_CALL: u16 = 0x0001;
    const VK_CONTROL: i32 = 0x11;
    const VK_MENU: i32 = 0x12;
    const VK_SHIFT: i32 = 0x10;

    let held = |vk: i32| unsafe { GetAsyncKeyState(vk) } as u16 & HELD != 0;
    if (chord.modifiers & MODIFIER_CTRL != 0 && !held(VK_CONTROL))
        || (chord.needs_alt() && !held(VK_MENU))
        || (chord.modifiers & MODIFIER_SHIFT != 0 && !held(VK_SHIFT))
    {
        // A modifier is up. Drop the latch so releasing the trigger later is not read as a press.
        *was_down = false;
        return false;
    }
    let state = unsafe { GetAsyncKeyState(chord.vk as i32) } as u16;
    let down = state & HELD != 0;
    let edge = (down && !*was_down) || state & PRESSED_SINCE_LAST_CALL != 0;
    *was_down = down;
    edge
}

#[cfg(not(windows))]
const fn keyboard_edge(_chord: er_hotkey_config::keys::Chord, _was_down: &mut bool) -> bool {
    false
}

/// `GameDataMan -> mainPlayerGameData -> equipGameData.itemReplenishStateTracker`.
unsafe fn resolve_tracker(base: usize) -> Option<usize> {
    let game_data_man = unsafe { safe_read_usize(base + GAME_DATA_MAN_GLOBAL_RVA) }?;
    let player =
        unsafe { safe_read_usize(game_data_man + GAME_DATA_MAN_MAIN_PLAYER_GAME_DATA_OFFSET) }?;
    unsafe { safe_read_usize(player + PLAYER_GAME_DATA_ITEM_REPLENISH_TRACKER_OFFSET) }
        .filter(|&tracker| tracker != 0)
}

/// Every item id the game would accept a replenish state for, read from the LIVE param tables.
///
/// Live rather than a table generated offline, so a modded regulation stays correct. `rows()`
/// PANICS on a table that has not streamed in yet -- `get_param_file` does
/// `holder.get_res_cap(0).expect(..)` -- so both holders are checked first; that panic killed two
/// earlier catalog runs elsewhere in this workspace.
#[cfg(windows)]
fn eligible_item_ids() -> Vec<i32> {
    use eldenring::cs::{EquipParamGoods, EquipParamWeapon, SoloParam, SoloParamRepository};
    use fromsoftware_shared::FromStatic;

    fn holder_ready<P: SoloParam>(repo: &SoloParamRepository) -> bool {
        repo.solo_param_holders
            .get(P::INDEX as usize)
            .and_then(|holder| holder.get_res_cap(0))
            .is_some()
    }

    // Safety: `instance()` yields a reference only when the singleton is populated, and every row
    // below is read, never written.
    let Ok(repo) = (unsafe { SoloParamRepository::instance() }) else {
        return Vec::new();
    };
    if !holder_ready::<EquipParamGoods>(repo) || !holder_ready::<EquipParamWeapon>(repo) {
        return Vec::new();
    }

    let mut ids = Vec::new();
    // Weapons: `GetEquipParamReplenishType` takes the high nibble 0 branch, so the row id is the
    // item id unchanged. In the stock regulation these are the 71 ammunition rows.
    for (row_id, row) in repo.rows::<EquipParamWeapon>() {
        if row.auto_replenish_type() != 0 {
            ids.push(row_id as i32);
        }
    }
    // Goods: high nibble 4.
    for (row_id, row) in repo.rows::<EquipParamGoods>() {
        if row.auto_replenish_type() != 0 {
            ids.push((row_id | GOODS_ITEM_ID_FLAG) as i32);
        }
    }
    ids
}

#[cfg(not(windows))]
fn eligible_item_ids() -> Vec<i32> {
    Vec::new()
}

/// One press: work out which way to go, write it, and optionally refill straight away.
unsafe fn run_cycle(
    base: usize,
    dialog: *mut core::ffi::c_void,
    source: &str,
    refill_immediately: bool,
) {
    let Some(tracker) = (unsafe { resolve_tracker(base) }) else {
        refill_log(format_args!(
            "{source}: press ignored: no ItemReplenishStateTracker yet"
        ));
        return;
    };
    let ids = eligible_item_ids();
    if ids.is_empty() {
        refill_log(format_args!(
            "{source}: press ignored: param tables not streamed in yet (no eligible items)"
        ));
        return;
    }

    let should_replenish: ShouldReplenishItemFn =
        unsafe { std::mem::transmute(base + SHOULD_REPLENISH_ITEM_RVA) };
    let set_state: SetStateFn = unsafe { std::mem::transmute(base + SET_STATE_RVA) };

    // Ask the game, rather than reading entries: `ShouldReplenishItem` applies the per-type
    // defaults for an item that has no entry at all (type 2 defaults ON, type 1 OFF), so it is the
    // only answer that matches what the storage box would show.
    let mut currently_on = 0u32;
    for id in &ids {
        let mut probe = *id;
        if unsafe { should_replenish(tracker, &raw mut probe) } {
            currently_on += 1;
        }
    }
    let eligible = u32::try_from(ids.len()).unwrap_or(u32::MAX);
    let target = next_target_state(eligible, currently_on);

    let mut outcome = MarkOutcome {
        eligible,
        ..MarkOutcome::default()
    };
    for id in &ids {
        let mut probe = *id;
        if unsafe { should_replenish(tracker, &raw mut probe) } == target {
            outcome.unchanged += 1;
            continue;
        }
        // An entry that already exists is flipped in place: no insert, no capacity risk, and the
        // sorted order that `FindItem` binary-searches is left undisturbed.
        if unsafe { flip_existing_entry(tracker, *id, target) } {
            outcome.flipped += 1;
            continue;
        }
        // Otherwise the native setter inserts one -- and this is the path that can DLPanic.
        let count = unsafe { tracker_count(tracker) };
        if count >= INSERT_CEILING {
            outcome.skipped_full += 1;
            continue;
        }
        let mut write = *id;
        unsafe { set_state(tracker, &raw mut write, target) };
        outcome.inserted += 1;
    }

    let cycles = CYCLES_RUN.fetch_add(1, Ordering::SeqCst) + 1;
    refill_log(format_args!(
        "{source}: cycle#{cycles} -> {} | eligible={} flipped={} inserted={} unchanged={} \
         skipped_full={} tracker_count={} depository_frames={}",
        if target { "REFILL ALL" } else { "NO REFILLS" },
        outcome.eligible,
        outcome.flipped,
        outcome.inserted,
        outcome.unchanged,
        outcome.skipped_full,
        unsafe { tracker_count(tracker) },
        DEPOSITORY_FRAMES.load(Ordering::Relaxed),
    ));
    if outcome.skipped_full > 0 {
        refill_log(format_args!(
            "{source}: {} item(s) left unmarked: the replenish tracker is near its 2048-entry \
             limit and inserting past it DLPanics the game",
            outcome.skipped_full
        ));
    }

    // Marking alone moves nothing: the transfer is `ReplanishItemsFromChest`, which vanilla runs
    // at a grace or after a load.
    if refill_immediately && target && outcome.wrote_anything() {
        let replenish: ReplanishItemsFromChestFn =
            unsafe { std::mem::transmute(base + REPLANISH_ITEMS_FROM_CHEST_RVA) };
        unsafe { replenish() };
        refill_log(format_args!(
            "{source}: cycle#{cycles} ran ReplanishItemsFromChest"
        ));
    }

    // Repaint. The rows were built BEFORE the write, so without this they keep showing the old
    // icons and the whole feature reads as inert -- which is exactly how it presented the first
    // time it was tested live. Vanilla calls the same function after its own single-item toggle,
    // so any cursor movement this causes is the game's own behaviour rather than something new.
    let refresh: DepositoryRefreshFn =
        unsafe { std::mem::transmute(base + DEPOSITORY_DIALOG_REFRESH_RVA) };
    unsafe { refresh(dialog, 1) };
    refill_log(format_args!(
        "{source}: cycle#{cycles} rebuilt the storage list"
    ));
}

/// Read `tracker->count`.
unsafe fn tracker_count(tracker: usize) -> u64 {
    unsafe { ((tracker + TRACKER_COUNT_OFFSET) as *const u64).read() }
}

/// Set `autoReplenish` on an entry that is already in the vector. False when there is none.
///
/// A linear scan rather than the game's binary search: 2048 entries is nothing next to a frame,
/// and reimplementing `FindItem`'s comparison (which is over UNSIGNED ids, and whose end sentinel
/// carries an alignment fudge) would be a second place for that subtlety to be got wrong.
unsafe fn flip_existing_entry(tracker: usize, item_id: i32, state: bool) -> bool {
    let count = unsafe { tracker_count(tracker) };
    let count = usize::try_from(count.min(crate::mark::TRACKER_CAPACITY)).unwrap_or(0);
    for index in 0..count {
        let entry = tracker + index * TRACKER_ENTRY_SIZE;
        if unsafe { (entry as *const i32).read() } == item_id {
            unsafe {
                ((entry + TRACKER_ENTRY_AUTO_REPLENISH_OFFSET) as *mut bool).write(state);
            }
            return true;
        }
    }
    false
}

/// Wait for the game module, then install. Called once from `DllMain`.
pub(crate) fn spawn(_module_base: usize) {
    let _ = std::thread::Builder::new()
        .name("er-refill-all".to_owned())
        .spawn(move || {
            let mut attempts = 0u64;
            loop {
                match game_module_base() {
                    Ok(base) => {
                        install(base);
                        break;
                    }
                    Err(err) => {
                        if attempts == 0 || attempts.is_multiple_of(4096) {
                            refill_log(format_args!(
                                "install: waiting for game module base: {err}"
                            ));
                        }
                        attempts = attempts.saturating_add(1);
                        std::thread::yield_now();
                    }
                }
            }
        });
}
