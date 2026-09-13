//! The native `05_010` list's own input and geometry, rebased so ten row models can represent a
//! listing of any length.
//!
//! Split out of `save_picker_menu` on 2026-09-11 when that file crossed the hard size limit. The
//! seam is not arbitrary: everything here is a detour on, or a direct read of, the game's own list
//! control -- the vertical menu-event ids, the cursor setter, the wheel delta, the point-to-index
//! hit test, and the grid's view base. The picker's own logic lives next door and calls into this.
//!
//! # Why the list is lied to rather than resized
//!
//! `ProfileSelect` stages exactly ten `ProfileSummary` rows, and the compact picker keeps it that
//! way: changing the native `GridControl` item count changes what the game believes about every
//! other surface built from the same template. So a long directory is a moving ten-row window over
//! the model, and these hooks are what keep the native control's idea of the cursor, the wheel and
//! the hit target consistent with a window that slides underneath it.

use std::ffi::c_void;
use std::sync::atomic::{AtomicUsize, Ordering};

use er_game_base::mem::{game_rva, game_rva_for_hook};
use er_hook::{MH_STATUS, MhHook};

use crate::host::append_autoload_debug;
use crate::save_picker_menu::*;

/// A press deferred at an extreme row, waiting for the native wrap it is about to cause, and how
/// many pump ticks it may wait. Four ticks is generous for a wrap the list performs on the very
/// next frame, and short enough that an unredeemed press cannot resurface as a phantom step later.
pub(crate) static SAVE_PICKER_PENDING_WRAP_MASK: AtomicUsize = AtomicUsize::new(0);
pub(crate) static SAVE_PICKER_PENDING_WRAP_TICKS: AtomicUsize = AtomicUsize::new(0);
pub(crate) const PENDING_WRAP_MAX_TICKS: usize = 4;

/// Selection moves with no key/pad/wheel behind them, i.e. the pointer; diagnostic only.
pub(crate) static SAVE_PICKER_POINTER_CURSOR_MOVES: AtomicUsize = AtomicUsize::new(0);
/// Times the grid scrolled its own view during a select and had to be put back.
#[allow(dead_code)] // Retained: Picker diagnostic counter, beside the sibling counters that are live.
pub(crate) static SAVE_PICKER_GRID_VIEW_RESTORES: AtomicUsize = AtomicUsize::new(0);
pub(crate) static SAVE_PICKER_GRID_GEOMETRY_LOGGED: AtomicUsize = AtomicUsize::new(0);

pub(crate) static SAVE_PICKER_SET_CURSOR_ORIG: AtomicUsize = AtomicUsize::new(0);
pub(crate) static SAVE_PICKER_SET_CURSOR_HOOK_INSTALLED: AtomicUsize = AtomicUsize::new(0);
pub(crate) static SAVE_PICKER_SET_CURSOR_NEUTRALISED: AtomicUsize = AtomicUsize::new(0);
/// Wheel detents the native grid refused (view base at a clamp) that this pump stepped instead.
pub(crate) static SAVE_PICKER_WHEEL_NATIVE_STEPS: AtomicUsize = AtomicUsize::new(0);

/// `FUN_14073bc10` detour: neutralise the ensure-visible base for every select on the picker's list.
///
/// The wheel step can zero the base around its own call, but the game makes this call itself on
/// every mouse hover and click, and those resets are what re-orient the list under a stationary
/// pointer. Hooking is the only place to reach them: the base and the index are in different spaces
/// (scrollbar model-space vs view-space 0..9) and only this function compares the two.
pub(crate) unsafe extern "system" fn save_picker_set_cursor_hook(list: usize, index: u32) -> u64 {
    let orig_addr = SAVE_PICKER_SET_CURSOR_ORIG.load(Ordering::SeqCst);
    if orig_addr == 0 {
        return 0;
    }
    let orig: unsafe extern "system" fn(usize, u32) -> u64 =
        unsafe { std::mem::transmute(orig_addr) };
    // Only the picker's own list, and only while the picker owns the screen: every other menu in
    // the game uses this grid the way it was designed and must keep its native scrolling.
    let dialog = save_picker_live_profile_dialog();
    let ours = dialog != 0
        && SAVE_PICKER_MODE_ACTIVE.load(Ordering::SeqCst) != 0
        && list == dialog + PROFILE_LOAD_DIALOG_ITEM_LIST_OFFSET;
    if !ours {
        return unsafe { orig(list, index) };
    }
    let before = unsafe { save_picker_grid_view_base(list) };
    if before != (0, 0) {
        unsafe { save_picker_set_grid_view_base(list, (0, 0)) };
    }
    let ret = unsafe { orig(list, index) };
    let after = unsafe { save_picker_grid_view_base(list) };
    if after != before {
        unsafe { save_picker_set_grid_view_base(list, before) };
        let n = SAVE_PICKER_SET_CURSOR_NEUTRALISED.fetch_add(1, Ordering::SeqCst) + 1;
        if n <= 20 || n.is_multiple_of(50) {
            append_autoload_debug(format_args!(
                "save-picker: native select neutralised #{n} index={index} view {before:?} (call left {after:?})"
            ));
        }
    }
    ret
}

pub fn install_save_picker_set_cursor_hook() {
    if SAVE_PICKER_SET_CURSOR_HOOK_INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    let Ok(addr) = game_rva_for_hook(MENU_ITEM_LIST_SET_CURSOR_RVA as u32) else {
        append_autoload_debug(format_args!(
            "save-picker: failed to resolve select-index rva 0x{MENU_ITEM_LIST_SET_CURSOR_RVA:x}"
        ));
        SAVE_PICKER_SET_CURSOR_HOOK_INSTALLED.store(0, Ordering::SeqCst);
        return;
    };
    match unsafe {
        MhHook::new(
            addr as *mut c_void,
            save_picker_set_cursor_hook as *mut c_void,
        )
    } {
        Ok(hook) => {
            SAVE_PICKER_SET_CURSOR_ORIG.store(hook.trampoline() as usize, Ordering::SeqCst);
            if let Err(status) = unsafe { hook.queue_enable() } {
                append_autoload_debug(format_args!(
                    "save-picker: queue_enable select-index failed: {status:?}"
                ));
                return;
            }
            match unsafe { er_hook::MH_ApplyQueued() } {
                MH_STATUS::MH_OK => {
                    leak_installed_hook(hook);
                    append_autoload_debug(format_args!(
                        "save-picker: hooked list select-index FUN_14073bc10 0x{addr:x}"
                    ));
                }
                status => append_autoload_debug(format_args!(
                    "save-picker: select-index MH_ApplyQueued failed: {status:?}"
                )),
            }
        }
        Err(status) => append_autoload_debug(format_args!(
            "save-picker: MhHook::new select-index failed: {status:?}"
        )),
    }
}

/// `FUN_140757c70` -- the only place the grid reads a wheel notch. Byte-verified unique in the
/// 1.16.2 deobf image at `0x140757c70` (`48 89 5c 24 08 57 48 83 ec 20 48 8b da 48 8b f9 ba 2c ..`).
///
/// It resolves the wheel to a `(col, row)` step from menu event ids `0x2c` (up, row -1) and `0x2d`
/// (down, row +1) via `FUN_14075d8f0`, and its only two callers are the grid mouse handler
/// `FUN_14073a5c0` and `FUN_140781460`.
pub(crate) const MENU_EVENT_WHEEL_DELTA_ACCESSOR_RVA: usize = 0x757c70;
pub(crate) static SAVE_PICKER_WHEEL_DELTA_ORIG: AtomicUsize = AtomicUsize::new(0);
pub(crate) static SAVE_PICKER_WHEEL_DELTA_HOOK_INSTALLED: AtomicUsize = AtomicUsize::new(0);
pub(crate) static SAVE_PICKER_WHEEL_DELTA_SILENCED: AtomicUsize = AtomicUsize::new(0);

/// The direction of the last notch the hook below silenced, as a `SAVE_PICKER_NAV_WHEEL_*_MASK`,
/// waiting for the edge-scroll pump to drain it.
///
/// A direction rather than a count, and deliberately: the accessor has two callers (the grid mouse
/// handler `FUN_14073a5c0` and the scrollbar handler `FUN_140781460`), so one detent can be read
/// twice in a frame. A bit set twice is still one step, which is the behaviour a player expects;
/// a counter would scroll two rows for one notch whenever both callers ran.
pub(crate) static SAVE_PICKER_NATIVE_WHEEL_EDGES: AtomicUsize = AtomicUsize::new(0);

/// Whether the detour below is live, i.e. whether the latch above is a wheel source at all.
///
/// The pump asks before falling back to the host's own latch, so the two can never both act on one
/// detent. They observe the same notch at different points -- the host reads `GetRawInputData`, this
/// reads the game's per-frame menu event -- so they can land on different pump ticks, and combining
/// them would scroll twice for one notch on exactly the ticks where they disagree.
pub(crate) fn save_picker_native_wheel_latch_live() -> bool {
    SAVE_PICKER_WHEEL_DELTA_ORIG.load(Ordering::SeqCst) != 0
}

/// Drain the latch. Returns the `SAVE_PICKER_NAV_WHEEL_*_MASK` bit, or 0 for no notch since the
/// last drain.
pub(crate) fn save_picker_take_native_wheel_edges() -> usize {
    SAVE_PICKER_NATIVE_WHEEL_EDGES.swap(0, Ordering::SeqCst)
}

/// Drop an undrained notch, for the pump tick that finds the picker gone. Without this a detent
/// spun as the browser closes is replayed into the listing the next time one opens.
pub(crate) fn save_picker_clear_native_wheel_edges() {
    SAVE_PICKER_NATIVE_WHEEL_EDGES.store(0, Ordering::SeqCst);
}

/// Wheel detents the edge-scroll pump actually acted on. The counterpart to
/// `SAVE_PICKER_WHEEL_DELTA_SILENCED`: the two being far apart is the shape of a silenced wheel
/// nobody owns, which is the defect run br-20260912-212001-9610 recorded.
pub(crate) static SAVE_PICKER_WHEEL_EDGES_CONSUMED: AtomicUsize = AtomicUsize::new(0);

/// The INTERLOCK: while the picker owns the screen, the game's own grid never sees a wheel notch.
///
/// Two mechanisms can scroll this list for one detent -- the native grid handler and this pump --
/// and the double scroll is simply both of them running. Every attempt to arbitrate them by timing
/// failed, and the live log says why: the handler acts later than the tick the detent arrives on and
/// later than the tick after it too (our step at `+107884ms`, the handler's move only visible at
/// `+107911ms`), so there is no tick on which the pump can ask "did the game already take this one?"
/// and get a true answer. Deferring by a fixed number of ticks just moves the guess.
///
/// So do not arbitrate: remove one of the two mechanisms. Zeroing the delta here makes the wheel
/// branch in `FUN_14073a5c0` (`if (delta.col != 0 || delta.row != 0)`) fall through, so the native
/// grid performs no view scroll and no cursor move at all, and the pump is the sole owner of the
/// wheel with no timing assumption anywhere. It also removes the reason the wheel was uneven in the
/// first place: the native step was gated on the grid's own view base being able to move, which is
/// false at a clamp, so the game was an unreliable owner even when it was the only one.
///
/// Scoped to the picker's own screen, and it silences a read rather than dropping the user's input:
/// the direction is latched into `SAVE_PICKER_NATIVE_WHEEL_EDGES` on the way past, so the detent
/// still reaches the picker. Every other menu keeps its native wheel exactly as designed.
///
/// # Why the latch is here and not only in the host
///
/// It used to be only in the host: the product reads `GetRawInputData` and answers
/// `take_nav_edges_for`. A standalone shell installs no such reader -- `er-save-game-row` leaves
/// every `SavePickerMenuHooks` field `None` -- so this detour silenced the game's wheel and handed
/// the notch to nobody, which made the picker's wheel strictly worse than vanilla's. Run
/// br-20260912-212001-9610 is 20 `silenced native wheel notch` lines with no step behind any of
/// them and `scroll_offset=0/25` throughout. Latching here fixes that for every host at once,
/// because this is the one place a notch is observed no matter who loaded the DLL.
///
/// `out[1]` carries the direction and `out[0]` is always 0 for a wheel: the accessor writes
/// `(0, -1)` for menu event `0x2c` and `(0, 1)` for `0x2d`, and zeroes both when neither is set.
pub(crate) unsafe extern "system" fn save_picker_wheel_delta_hook(
    msg: usize,
    out: *mut i32,
) -> *mut i32 {
    let orig_addr = SAVE_PICKER_WHEEL_DELTA_ORIG.load(Ordering::SeqCst);
    if orig_addr == 0 {
        return out;
    }
    let orig: unsafe extern "system" fn(usize, *mut i32) -> *mut i32 =
        unsafe { std::mem::transmute(orig_addr) };
    let ret = unsafe { orig(msg, out) };
    let owned = save_picker_live_profile_dialog() != 0
        && SAVE_PICKER_MODE_ACTIVE.load(Ordering::SeqCst) != 0;
    if !owned || out.is_null() {
        return ret;
    }
    let row_delta = unsafe { out.add(1).read_unaligned() };
    let had_notch = unsafe { out.read_unaligned() != 0 } || row_delta != 0;
    if had_notch {
        unsafe {
            out.write_unaligned(0);
            out.add(1).write_unaligned(0);
        }
        // Latch the direction before the silence is announced, so a run whose log ends mid-frame
        // still shows the notch was handed on rather than merely dropped.
        //
        // Only a row delta is a direction. The accessor writes `out[0] = 0` on every path, so a
        // column-only notch is not a shape it produces, and latching one would invent a direction
        // out of a value that carries none.
        let edge = match row_delta.signum() {
            1 => SAVE_PICKER_NAV_WHEEL_DOWN_MASK,
            -1 => SAVE_PICKER_NAV_WHEEL_UP_MASK,
            _ => 0,
        };
        SAVE_PICKER_NATIVE_WHEEL_EDGES.fetch_or(edge, Ordering::SeqCst);
        let n = SAVE_PICKER_WHEEL_DELTA_SILENCED.fetch_add(1, Ordering::SeqCst) + 1;
        if n <= 20 || n.is_multiple_of(50) {
            append_autoload_debug(format_args!(
                "save-picker: silenced native wheel notch #{n} row_delta={row_delta} latched=0x{edge:x} (the pump owns the wheel)"
            ));
        }
    }
    ret
}

pub fn install_save_picker_wheel_delta_hook() {
    if SAVE_PICKER_WHEEL_DELTA_HOOK_INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    let Ok(addr) = game_rva_for_hook(MENU_EVENT_WHEEL_DELTA_ACCESSOR_RVA as u32) else {
        append_autoload_debug(format_args!(
            "save-picker: failed to resolve wheel-delta rva 0x{MENU_EVENT_WHEEL_DELTA_ACCESSOR_RVA:x}"
        ));
        SAVE_PICKER_WHEEL_DELTA_HOOK_INSTALLED.store(0, Ordering::SeqCst);
        return;
    };
    match unsafe {
        MhHook::new(
            addr as *mut c_void,
            save_picker_wheel_delta_hook as *mut c_void,
        )
    } {
        Ok(hook) => {
            SAVE_PICKER_WHEEL_DELTA_ORIG.store(hook.trampoline() as usize, Ordering::SeqCst);
            if let Err(status) = unsafe { hook.queue_enable() } {
                append_autoload_debug(format_args!(
                    "save-picker: queue_enable wheel-delta failed: {status:?}"
                ));
                return;
            }
            match unsafe { er_hook::MH_ApplyQueued() } {
                MH_STATUS::MH_OK => {
                    leak_installed_hook(hook);
                    append_autoload_debug(format_args!(
                        "save-picker: hooked wheel-delta FUN_140757c70 0x{addr:x}"
                    ));
                }
                status => append_autoload_debug(format_args!(
                    "save-picker: wheel-delta MH_ApplyQueued failed: {status:?}"
                )),
            }
        }
        Err(status) => append_autoload_debug(format_args!(
            "save-picker: MhHook::new wheel-delta failed: {status:?}"
        )),
    }
}

/// Move the picker's selection onto `model_row` through the list's own select primitive.
///
/// This calls `FUN_14073bc10`, the select primitive, which is the same call the grid's own mouse hit
/// test makes. It moves the cell cursor and carries the chrome with it, and it re-enters the detour
/// above so the view base stays pinned exactly as it does for a hover or a click.
///
/// The wrapper around it, `FUN_140738d40`, additionally fires the owning dialog's
/// selection-changed callbacks, and it must not be used here: on run br-20260912-231628-6a27 it
/// stopped the picker's scrolling outright. See bd
/// `select-and-notify-wrapper-kills-picker-scrolling-2026-09-12`.
///
/// The grid refuses an index at or past its item count (`list+0xd0`) and the refusal is silent:
/// `FUN_14073bc10` returns 0, writes no cursor and shows no chrome. A short directory stages fewer
/// than ten occupied records and the rebuild sets the count from them, so that is reachable
/// whenever the window holds empty slots. The row is clamped into the count here, because a
/// selection on a row the grid does not have is a selection nobody can see.
///
/// Returns the index the grid accepted, or `None` when it accepted nothing.
pub(crate) unsafe fn save_picker_select_native_row(
    dialog: usize,
    model_row: usize,
    reason: &str,
) -> Option<i32> {
    let list = dialog + PROFILE_LOAD_DIALOG_ITEM_LIST_OFFSET;
    let wanted = i32::try_from(model_row)
        .ok()?
        .checked_add(PROFILE_SELECT_NATIVE_ROW_MODEL_OFFSET)?;
    let count = unsafe { *((list + GRID_CONTROL_ITEM_COUNT_OFFSET) as *const i32) };
    if count <= 0 {
        append_autoload_debug(format_args!(
            "save-picker: select declined, the grid holds no items reason={reason} wanted={wanted}"
        ));
        return None;
    }
    let index = wanted.clamp(0, count - 1);
    let select = game_rva(MENU_ITEM_LIST_SET_CURSOR_RVA as u32).ok()?;
    let select: unsafe extern "system" fn(usize, u32) -> u64 =
        unsafe { std::mem::transmute(select) };
    let ret = unsafe { select(list, u32::try_from(index).ok()?) };
    if ret != 1 {
        append_autoload_debug(format_args!(
            "save-picker: select declined reason={reason} wanted={wanted} index={index} count={count} ret={ret}; the highlight stays where it was"
        ));
        return None;
    }
    // Keep the pump's edge sampling honest: the next tick compares against this, and leaving the
    // pre-step row here would read our own step back as a native move and swallow the next input.
    SAVE_PICKER_EDGE_SCROLL_PREV_CURSOR.store(
        usize::try_from(index).unwrap_or(EDGE_SCROLL_NO_PREV_CURSOR),
        Ordering::SeqCst,
    );
    if index != wanted {
        append_autoload_debug(format_args!(
            "save-picker: select clamped into the item count reason={reason} wanted={wanted} index={index} count={count}"
        ));
    }
    Some(index)
}

/// Move the picker's selection one row for a wheel detent the native grid declined to act on.
pub(crate) unsafe fn save_picker_wheel_step_native_cursor(
    dialog: usize,
    model_row: usize,
    from_cursor: i32,
) -> i32 {
    let Some(index) = (unsafe { save_picker_select_native_row(dialog, model_row, "wheel-step") })
    else {
        return from_cursor;
    };
    let n = SAVE_PICKER_WHEEL_NATIVE_STEPS.fetch_add(1, Ordering::SeqCst) + 1;
    if n <= 20 || n.is_multiple_of(25) {
        append_autoload_debug(format_args!(
            "save-picker: wheel step #{n} the grid declined from={from_cursor} to_index={index}"
        ));
    }
    index
}

/// `FUN_140736c90(grid, point)` -- the grid's pointer hit test, byte-verified unique at
/// `0x140736c90` in the 1.16.2 deobf image.
pub(crate) const MENU_ITEM_LIST_POINT_TO_INDEX_RVA: usize = 0x736c90;
pub(crate) static SAVE_PICKER_HIT_TEST_ORIG: AtomicUsize = AtomicUsize::new(0);
pub(crate) static SAVE_PICKER_HIT_TEST_HOOK_INSTALLED: AtomicUsize = AtomicUsize::new(0);
pub(crate) static SAVE_PICKER_HIT_TEST_REBASED: AtomicUsize = AtomicUsize::new(0);

/// Neutralise the view base for the grid's pointer hit test, the same way the select hook does for
/// the select itself.
///
/// The hit test walks the visible cells and turns the one under the pointer into an absolute item
/// index by adding the view base, then discards the hit if that index is past the item count:
///
///     140736d41  MOV  R11D, [RSI + 0x348]   ; view row base
///     140736d80  LEA  EDI, [R10 + R11*1]    ; view row + base
///     140736daf  CMP  [RSI + 0xd0], EDI     ; count vs index
///     140736db5  JLE  ...                   ; index >= count -> report no hit
///
/// The picker keeps its model's scroll offset in that base so the native scrollbar thumb tracks a
/// listing far longer than the ten staged records (`save-picker: native scrollbar sync`). For the
/// hit test that offset is poison: with base 10 against 10 records every visible cell computes an
/// index >= count, so the pointer hits nothing, nothing is selected, and the game's click
/// activation has nothing to act on. Clicking therefore worked only while the scrollbar sat at the
/// very top, where the base happens to be 0 -- reported 2026-08-12, and the same shape as the wheel
/// dying at a clamped base.
///
/// Zeroing the base for the duration of the call makes the hit test return a view-relative index
/// `0..9`, which is exactly the space the ten staged records live in and the space the select hook
/// already leaves `+0xd4` in. The base is restored immediately afterwards, so the scrollbar thumb is
/// unaffected.
pub(crate) unsafe extern "system" fn save_picker_hit_test_hook(list: usize, point: usize) -> u32 {
    let orig_addr = SAVE_PICKER_HIT_TEST_ORIG.load(Ordering::SeqCst);
    if orig_addr == 0 {
        return u32::MAX;
    }
    let orig: unsafe extern "system" fn(usize, usize) -> u32 =
        unsafe { std::mem::transmute(orig_addr) };
    let dialog = save_picker_live_profile_dialog();
    let ours = dialog != 0
        && SAVE_PICKER_MODE_ACTIVE.load(Ordering::SeqCst) != 0
        && list == dialog + PROFILE_LOAD_DIALOG_ITEM_LIST_OFFSET;
    if !ours {
        return unsafe { orig(list, point) };
    }
    let before = unsafe { save_picker_grid_view_base(list) };
    if before == (0, 0) {
        return unsafe { orig(list, point) };
    }
    unsafe { save_picker_set_grid_view_base(list, (0, 0)) };
    let ret = unsafe { orig(list, point) };
    unsafe { save_picker_set_grid_view_base(list, before) };
    let n = SAVE_PICKER_HIT_TEST_REBASED.fetch_add(1, Ordering::SeqCst) + 1;
    if n <= 20 || n.is_multiple_of(100) {
        append_autoload_debug(format_args!(
            "save-picker: hit test rebased #{n} view {before:?} -> (0, 0) index={ret}"
        ));
    }
    ret
}

pub fn install_save_picker_hit_test_hook() {
    if SAVE_PICKER_HIT_TEST_HOOK_INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    let Ok(addr) = game_rva_for_hook(MENU_ITEM_LIST_POINT_TO_INDEX_RVA as u32) else {
        append_autoload_debug(format_args!(
            "save-picker: failed to resolve hit-test rva 0x{MENU_ITEM_LIST_POINT_TO_INDEX_RVA:x}"
        ));
        SAVE_PICKER_HIT_TEST_HOOK_INSTALLED.store(0, Ordering::SeqCst);
        return;
    };
    match unsafe {
        MhHook::new(
            addr as *mut c_void,
            save_picker_hit_test_hook as *mut c_void,
        )
    } {
        Ok(hook) => {
            SAVE_PICKER_HIT_TEST_ORIG.store(hook.trampoline() as usize, Ordering::SeqCst);
            if let Err(status) = unsafe { hook.queue_enable() } {
                append_autoload_debug(format_args!(
                    "save-picker: queue_enable hit-test failed: {status:?}"
                ));
                return;
            }
            match unsafe { er_hook::MH_ApplyQueued() } {
                MH_STATUS::MH_OK => {
                    leak_installed_hook(hook);
                    append_autoload_debug(format_args!(
                        "save-picker: hooked pointer hit test FUN_140736c90 0x{addr:x}"
                    ));
                }
                status => append_autoload_debug(format_args!(
                    "save-picker: hit-test MH_ApplyQueued failed: {status:?}"
                )),
            }
        }
        Err(status) => append_autoload_debug(format_args!(
            "save-picker: MhHook::new hit-test failed: {status:?}"
        )),
    }
}

/// The grid's own view-scroll base as `(column, row)`.
pub(crate) unsafe fn save_picker_grid_view_base(list: usize) -> (i32, i32) {
    unsafe {
        (
            *((list + GRID_CONTROL_VIEW_COL_BASE_OFFSET) as *const i32),
            *((list + GRID_CONTROL_VIEW_ROW_BASE_OFFSET) as *const i32),
        )
    }
}

pub(crate) unsafe fn save_picker_set_grid_view_base(list: usize, base: (i32, i32)) {
    unsafe {
        *((list + GRID_CONTROL_VIEW_COL_BASE_OFFSET) as *mut i32) = base.0;
        *((list + GRID_CONTROL_VIEW_ROW_BASE_OFFSET) as *mut i32) = base.1;
    }
}

/// Log the grid's index space once per picker session: the select call bounds-checks against these,
/// and whether the cursor index is absolute or view-relative depends on them.
pub(crate) unsafe fn save_picker_log_grid_geometry_once(list: usize) {
    if SAVE_PICKER_GRID_GEOMETRY_LOGGED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    let (count, cols, rows) = unsafe {
        (
            *((list + GRID_CONTROL_ITEM_COUNT_OFFSET) as *const i32),
            *((list + GRID_CONTROL_COLUMNS_OFFSET) as *const i32),
            *((list + GRID_CONTROL_ROWS_OFFSET) as *const i32),
        )
    };
    let view = unsafe { save_picker_grid_view_base(list) };
    append_autoload_debug(format_args!(
        "save-picker: grid geometry count={count} cols={cols} rows={rows} view_base={view:?}"
    ));
}
