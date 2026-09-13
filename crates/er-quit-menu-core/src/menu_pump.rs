//! The menu pump the link field needs, for a host that has no other reason to own one.
//!
//! `CS::MenuWindowJob::Run` is the game's menu pump executing one window. Three things have to
//! happen inside it and nowhere else:
//!
//! * the field's `CS::SoftwareKeyboardJob` is built and submitted there. Building or submitting a
//!   `MenuJob` from a game task instead produced the Scaleform race behind the non-deterministic
//!   execute faults (bd `system-quit-return-title-scaleform-race`);
//! * the field's `SceneObjProxy` resolves are only valid while its own window is running, which is
//!   what the end-caret and the live clipboard mirror need;
//! * a window that stopped running is the only signal a field closed by the back action emits, so
//!   the abandoned-latch sweep has to run on the same cadence.
//!
//! The product drives all three from its own post-run hook, which also owns the save picker, the
//! return-title chain and the in-world pause menu. A standalone shell has none of those, so this is
//! the narrow version: one detour, one resource name, and nothing else touched.

use std::sync::atomic::{AtomicUsize, Ordering};

use er_game_base::mem::{game_module_base, game_rva_for_hook, safe_read_i32, safe_read_usize};

use crate::build_url_editor::{build_url_editor_menu_pump, build_url_editor_window_run};
use crate::host::{append_autoload_debug, system_quit_save_swap_restore_profile_summary};
use crate::scaleform_proxy::{
    apply_build_url_editor_window_position, apply_path_editor_window_position,
};
use crate::software_keyboard::{
    BUILD_URL_TEXT_INPUT_RESOURCE_NAME, TEXT_INPUT_RESOURCE_NAME,
    build_url_note_editor_window_state, save_picker_note_path_editor_window_state,
    save_picker_path_editor_completion_tick, text_input_02_990_window_is_live,
};
use crate::system_windows::{self, SystemWindowHooks};

/// `CS::MenuWindowJob::Run`. Declared once in `er-title-flow`, where the product's own detour on the
/// same address reads it, and derived here.
const MENU_WINDOW_JOB_RUN_RVA: u32 = er_title_flow::PAB_NODE_UPDATE_RVA;
/// `MenuWindowJob.resourceName`, the `wchar_t*` the game itself uses to tell its windows apart.
const MENU_WINDOW_JOB_RESOURCE_NAME_60_OFFSET: usize = 0x60;
const MENU_WINDOW_JOB_OWNING_WINDOW_OFFSET: usize =
    er_title_flow::MENU_WINDOW_JOB_OWNING_WINDOW_OFFSET;
const MSGBOX_JOB_RESULT_STATE_1E8_OFFSET: usize = er_title_flow::MSGBOX_JOB_RESULT_STATE_1E8_OFFSET;

static RUN_ORIG: AtomicUsize = AtomicUsize::new(0);
static RUN_INSTALLED: AtomicUsize = AtomicUsize::new(0);
/// Whether this host armed a character row, which is what makes the ProfileSelect work below its
/// business. A build-rows-only shell leaves it 0 and the hook behaves exactly as it did before.
static CHARACTER_ROWS_ARMED: AtomicUsize = AtomicUsize::new(0);

/// Tell the pump a character row is armed, so it owns the System-window hide behind ProfileSelect.
pub fn set_character_rows_armed(armed: bool) {
    CHARACTER_ROWS_ARMED.store(usize::from(armed), Ordering::SeqCst);
}

/// Whether this host owns the Save Game row, which is what makes the browser this pump's business.
/// A host whose own pump already drives the flow leaves it 0 and nothing here changes.
static SAVE_FLOW_ROW_ARMED: AtomicUsize = AtomicUsize::new(0);

/// Tell the pump the Save Game row is armed here, so it opens the row's destination browser.
pub fn set_save_game_row_armed(armed: bool) {
    SAVE_FLOW_ROW_ARMED.store(usize::from(armed), Ordering::SeqCst);
}

/// Whether this host opens `05_010_ProfileSelect` at all, by either route.
///
/// The hide/restore below used to ask `CHARACTER_ROWS_ARMED` alone, and the Save Game row opens the
/// same window: run br-20260912-201454-d12a opened the destination browser underneath the pause
/// menu, which stayed drawn over it and kept taking input, because a Save Game-only shell arms no
/// character row and the whole System-window half was skipped. The question the hide has to answer
/// is "does this host put a ProfileSelect on screen", not "which row did it come from".
fn host_opens_profile_select() -> bool {
    CHARACTER_ROWS_ARMED.load(Ordering::SeqCst) != 0 || save_game_row_owns_this_picker()
}

/// The Save Game row's half of the question, and it needs a second term.
///
/// A character row is the only thing that opens a `ProfileSelect` in a host that armed one, so
/// "armed" answers it there. The Save Game row is not: the game opens its own `05_010` at the title
/// whenever a save has to be chosen, and on run br-20260912-202820-1329 -- a run with no autoload
/// and no row press at all -- the arm-only test fired the System-window hide against that title
/// screen, 12 times before anyone touched the menu, with `top=0x0 option=0x0` because at the title
/// there is no pause menu to hide. Hiding is only ever right for the picker this row opened, so ask
/// the latch the open itself sets rather than the latch the arm sets.
fn save_game_row_owns_this_picker() -> bool {
    SAVE_FLOW_ROW_ARMED.load(Ordering::SeqCst) != 0
        && er_telemetry_core::counters::SAVE_PICKER_MODE_ACTIVE.load(Ordering::SeqCst) != 0
}

/// The windows the hide/restore has to know about, by the game's own resource name.
const INGAME_TOP_RESOURCE_NAME: &str = "02_000_IngameTop";
const OPTION_SETTING_RESOURCE_NAME: &str = "02_040_OptionSetting";
const OPTION_SETTING_TRIAL_RESOURCE_NAME: &str = "02_041_OptionSetting_Trial";
const PROFILE_SELECT_RESOURCE_NAME: &str =
    crate::profile_select_movie_key::NATIVE_PROFILE_SELECT_RESOURCE_NAME;
/// The same window opened under the picker's private Scaleform cache key. It is the identical
/// `CS::ProfileSelect` dialog -- only the movie the loader was asked for differs -- so every
/// per-frame decision below has to answer for it too, or a picker whose key was rebound would run
/// with the System windows still drawn underneath it.
const PICKER_PROFILE_SELECT_RESOURCE_NAME: &str =
    crate::profile_select_movie_key::PICKER_PROFILE_SELECT_RESOURCE_NAME;
/// `MenuWindowJob.owningWindow` for the System windows, which is a different field from the one the
/// software-keyboard windows are read through.
const MENU_WINDOW_JOB_WINDOW_130_OFFSET: usize = 0x130;

/// How many dead owning windows the run hook has seen, so the log line below stays bounded.
static DEAD_OWNER_COUNT: AtomicUsize = AtomicUsize::new(0);

/// A `MenuWindowJob`'s owning window, or 0 when the pointer at `+0x130` is not a live `MenuWindow`.
///
/// That pointer is read once per presented frame and stored in a tracker the hide and the restore
/// read on some later frame, so it is a sample rather than a borrow. A window freed between the
/// store and the use still passes an "is it heap-like" screen, and the restore then hands it to a
/// game constructor that dereferences `window+0x188`. Measured on run br-20260912-183117-e19a: the
/// tracked `02_000_IngameTop` read a vtable of 0 and the tracked `02_040_OptionSetting` read
/// 0x884ac480, a heap address 0xc400 from the window itself. A live one reads a game vtable
/// (0x142b00620 / 0x142b16b48 / 0x142b25a78 on run br-20260912-034506-45db), which is the screen.
fn live_menu_window(owner: usize) -> usize {
    if owner == 0 {
        return 0;
    }
    let Ok(base) = game_module_base() else {
        return 0;
    };
    // Safety: the fault-safe reader answers `None` rather than faulting on a freed window.
    let vt = unsafe { safe_read_usize(owner) }.unwrap_or(0);
    if er_game_base::mem::vtable_in_game_image(vt, base) {
        owner
    } else {
        0
    }
}

/// Report a dead owning window without flooding: this runs once per presented frame.
fn note_dead_owner(filename: &str, owner: usize) {
    const FIRST_FEW: usize = 8;
    const THEREAFTER: usize = 512;
    let seen = DEAD_OWNER_COUNT.fetch_add(1, Ordering::SeqCst);
    if seen >= FIRST_FEW && seen % THEREAFTER != 0 {
        return;
    }
    append_autoload_debug(format_args!(
        "system-quit-dup: MenuWindowJob::Run resource='{filename}' owning window 0x{owner:x} is not a live MenuWindow (seen={seen}); leaving the tracker alone rather than stamping a dead window the restore would hand to the game"
    ));
}

/// Run the Save Game destination browser's pump work for a host that has no other menu pump.
///
/// The product drives this from its own stepper. A shell that carries only the Save Game row has
/// nothing else running inside `MenuWindowJob::Run`, and the browser is opened by submitting a
/// `MenuJob` -- which is legal only there. Calling it twice in a frame is harmless: the open latch
/// is cleared by the open that discharges it, and every maintenance step below is a no-op while
/// nothing is staged.
///
/// # Safety
///
/// Menu-pump context.
pub unsafe fn save_flow_window_run() {
    // One line, once: whether this detour reaches the pump at all. Run br-20260912-200225-784a
    // staged a browse request and then logged nothing further -- neither the browser opening nor
    // either of the two refusals inside it -- which leaves two stories that look identical from
    // outside, a pump that never ran and a pump that ran against an empty latch. This tells them
    // apart on the next run.
    if SAVE_FLOW_PUMP_REACHED.swap(1, Ordering::SeqCst) == 0 {
        append_autoload_debug(format_args!(
            "save-flow: MenuWindowJob::Run reached the Save Game pump for the first time; open-picker latch={}",
            er_telemetry_core::counters::SAVE_DEST_OPEN_PICKER_PENDING.load(Ordering::SeqCst)
        ));
    }
    unsafe { crate::save_picker_menu::save_flow_menu_pump() };
}

/// One-shot latch for the line above.
static SAVE_FLOW_PUMP_REACHED: AtomicUsize = AtomicUsize::new(0);

/// Detect a destination browser that closed by Back or Escape, and undo what its opening did.
///
/// Every other way out of the picker discharges itself: a picked file, a failed submit, a failed
/// resubmit. Back and Escape do not -- the game tears its own `05_010` window down and nothing in
/// this crate hears about it. On run br-20260912-203713-3e2e that left two things wrong at once and
/// the second is the one the player feels: `SAVE_PICKER_MODE_ACTIVE` stayed 1, so the pump kept
/// syncing a scrollbar on the dead dialog (32,768 skips and climbing against `0x1cbe64080`), and the
/// System windows stayed hidden, so the pause menu could not be reopened at all -- there is no
/// restore line anywhere after the `hid_top=true hid_option=true` that opened it.
///
/// The edge is the window itself, not a latch someone has to remember to set: a live `MenuWindow`'s
/// first qword is a game vtable, and a torn-down one is not, which is the same screen
/// [`live_menu_window`] already applies to an owning window. The product reaches the same state
/// through its own finalizer detour (`take_finalized_profile_select`); a shell installs none, so it
/// asks the window directly.
///
/// # Safety
///
/// Menu-pump context.
unsafe fn note_picker_window_closed() {
    if er_telemetry_core::counters::SAVE_PICKER_MODE_ACTIVE.load(Ordering::SeqCst) == 0 {
        return;
    }
    let window =
        er_telemetry_core::counters::SYSTEM_QUIT_PROFILE_SELECT_WINDOW.load(Ordering::SeqCst);
    // Not yet stamped is not the same as closed: the window is tracked on its first `Run`, and
    // between the submit and that frame there is nothing to test.
    if window == 0 || live_menu_window(window) != 0 {
        return;
    }
    er_telemetry_core::counters::SAVE_PICKER_MODE_ACTIVE.store(0, Ordering::SeqCst);
    er_telemetry_core::counters::SAVE_PICKER_DEST_MODE.store(0, Ordering::SeqCst);
    er_telemetry_core::counters::SYSTEM_QUIT_PROFILE_SELECT_WINDOW.store(0, Ordering::SeqCst);
    *er_save_picker_core::model::active_save_picker_lock() = None;
    // The picker drew its rows by writing them into the live `CS::ProfileSummary`, and this is the
    // frame its window stopped existing -- so this is where the game's own character records go
    // back. The `profile_select_window_run` restore does not cover it: that one fires on an
    // owner-cleared `Run` tick, and a destination browser torn down this way never reaches one.
    // Run br-20260913-021311-a481 staged five records, closed here, and had no restore line
    // anywhere in its log; the save that followed wrote the browse labels into the container.
    unsafe { system_quit_save_swap_restore_profile_summary("dest-picker-window-gone") };
    append_autoload_debug(format_args!(
        "save-dest-picker: the browser window 0x{window:x} is gone (back/escape); cleared the picker state and restoring the System windows it hid"
    ));
    if let Ok(base) = game_module_base() {
        unsafe {
            system_windows::restore_real_system_windows(
                base,
                "standalone-picker-closed-by-back",
                &SystemWindowHooks::NONE,
            )
        };
    }
}

/// The System-window half of the post-run body, for a host that armed a character row.
///
/// This is the work that makes a standalone **Load Character** press produce a picker the player
/// can actually use. Without it the pause menu the picker opened over stays drawn and keeps taking
/// input, because nothing in a shell was ever told those windows exist.
///
/// # Safety
///
/// Menu-pump context, with `job` a live `MenuWindowJob`.
unsafe fn profile_select_window_run(job: usize, filename: &str) {
    let raw_owner =
        unsafe { safe_read_usize(job + MENU_WINDOW_JOB_WINDOW_130_OFFSET) }.unwrap_or(0);
    let owner = live_menu_window(raw_owner);
    if raw_owner != 0 && owner == 0 {
        note_dead_owner(filename, raw_owner);
    }
    match filename {
        INGAME_TOP_RESOURCE_NAME => {
            er_telemetry_core::counters::SYSTEM_QUIT_INGAME_TOP_WINDOW
                .store(owner, Ordering::SeqCst);
        }
        OPTION_SETTING_RESOURCE_NAME | OPTION_SETTING_TRIAL_RESOURCE_NAME => {
            er_telemetry_core::counters::SYSTEM_QUIT_OPTION_SETTING_WINDOW
                .store(owner, Ordering::SeqCst);
        }
        PROFILE_SELECT_RESOURCE_NAME | PICKER_PROFILE_SELECT_RESOURCE_NAME => {
            er_telemetry_core::counters::SYSTEM_QUIT_PROFILE_SELECT_WINDOW
                .store(owner, Ordering::SeqCst);
            er_telemetry_core::counters::PROFILE_SELECT_WINDOW_RUN_TICKS
                .fetch_add(1, Ordering::SeqCst);
            let Ok(base) = game_module_base() else {
                return;
            };
            if owner == 0 {
                // The picker renders its browse rows by writing them into the live
                // `CS::ProfileSummary`, so the window going away is the moment the game's own
                // records have to come back. Nothing else in a shell is that moment: the open-path
                // restores only cover re-entry, and a player who closes the picker and quits to the
                // title otherwise reaches a `Load Game` list of folder names.
                unsafe { system_quit_save_swap_restore_profile_summary("profile-owner-cleared") };
                unsafe {
                    system_windows::restore_real_system_windows(
                        base,
                        "standalone-profile-owner-cleared",
                        &SystemWindowHooks::NONE,
                    )
                };
            } else {
                unsafe {
                    system_windows::hide_real_system_windows(
                        base,
                        "standalone-hide-after-profile-select-run",
                    )
                };
            }
        }
        _ => {}
    }
}

/// Read a NUL-terminated UTF-16 resource name, bounded.
fn read_wide_resource_name(ptr: usize) -> String {
    const MAX_UNITS: usize = 64;
    if ptr < 0x10000 {
        return String::new();
    }
    let mut units = Vec::new();
    for idx in 0..MAX_UNITS {
        // Safety: the fault-safe reader answers `None` rather than faulting on a wild pointer.
        let unit = unsafe { er_game_base::mem::safe_read_u16(ptr + idx * 2) }.unwrap_or(0);
        if unit == 0 {
            break;
        }
        units.push(unit);
    }
    // UTF-8 Lossy: a resource name is the game's own ASCII identifier; an unpaired surrogate here
    // would mean the pointer was not a resource name at all, and the comparison below then fails,
    // which is the right answer.
    String::from_utf16_lossy(&units)
}

/// Post-run work for the link field's own window, plus the pump step that submits a queued field.
///
/// # Safety
///
/// Installed by `er-hook`; the game calls it on its menu thread with a live `MenuWindowJob`.
unsafe extern "system" fn quit_menu_window_job_run_hook(
    job: usize,
    a: usize,
    b: usize,
    c: usize,
) -> usize {
    let orig = RUN_ORIG.load(Ordering::SeqCst);
    if orig == 0 {
        return 0;
    }
    // Safety: the union publishes either the game trampoline or the next handler in the chain,
    // both of which take four registers under the union signature.
    let next: er_hook::UnionFn = unsafe { std::mem::transmute(orig) };
    let ret = unsafe { next(job, a, b, c) };

    // The Save Game row's destination browser, if this host owns that row. It is opened by
    // submitting a `MenuJob`, which is legal only inside this call -- and on run
    // br-20260912-194149-11e9 the standalone shell had this detour installed and still never
    // opened one, because nothing here asked for it: the press staged a request at
    // `SAVE_FLOW_STAGE_DEST_BROWSE` and every later press read the stage as busy and was ignored.
    if SAVE_FLOW_ROW_ARMED.load(Ordering::SeqCst) != 0 {
        unsafe { save_flow_window_run() };
        unsafe { note_picker_window_closed() };
    }

    let filename_ptr =
        unsafe { safe_read_usize(job + MENU_WINDOW_JOB_RESOURCE_NAME_60_OFFSET) }.unwrap_or(0);
    // The picker's current-path editor. Its movie is derived with the backing plate and frame
    // alpha-zeroed -- the picker's own `CurrentPath` button is the frame -- so the window has to be
    // put where that button is or the field renders as a bare text run in the top-left corner of
    // the screen, which is what run br-20260912-204404-206d showed once a shell finally served the
    // movie at all.
    if read_wide_resource_name(filename_ptr) == TEXT_INPUT_RESOURCE_NAME {
        let owner =
            unsafe { safe_read_usize(job + MENU_WINDOW_JOB_OWNING_WINDOW_OFFSET) }.unwrap_or(0);
        if owner != 0 {
            // The window has to be recorded, not just positioned. Its result state is the native
            // owner of the edit session: `save_picker_note_path_editor_window_state` releases the
            // `SoftwareKeyboardJob` on the terminal transition, which is the only thing that ever
            // tells this crate a Back closed the field.
            //
            // Until run br-20260912-210244-31fb this branch positioned the window and told nobody,
            // so in a shell -- where no other detour calls that function -- ownership was never
            // released. The field then refused every later open, and the drive strip went with it:
            // `save_picker_menu_pump_drive_strip_mouse` bails on its first line while the editor
            // reads as active, so mouse and arrows both stopped reaching the drive cells. One
            // stuck job, three symptoms, including the path text that never came back because the
            // cancel that restages the rows never ran.
            let state = unsafe { safe_read_i32(owner + MSGBOX_JOB_RESULT_STATE_1E8_OFFSET) }
                .unwrap_or_default();
            if save_picker_note_path_editor_window_state(owner, state)
                && let Ok(base) = game_module_base()
            {
                unsafe { apply_path_editor_window_position(base, owner) };
                // Inline completion, in the only context where the field's child proxies resolve.
                unsafe { save_picker_path_editor_completion_tick(base, owner) };
            }
        }
    }
    if read_wide_resource_name(filename_ptr) == BUILD_URL_TEXT_INPUT_RESOURCE_NAME {
        let owner =
            unsafe { safe_read_usize(job + MENU_WINDOW_JOB_OWNING_WINDOW_OFFSET) }.unwrap_or(0);
        if owner != 0 {
            let state = unsafe { safe_read_i32(owner + MSGBOX_JOB_RESULT_STATE_1E8_OFFSET) }
                .unwrap_or_default();
            if build_url_note_editor_window_state(owner, state)
                && let Ok(base) = game_module_base()
            {
                unsafe { apply_build_url_editor_window_position(base, owner) };
                // A window is only worth touching while it is still running; a terminal result
                // means its `SceneObjProxy` teardown has begun and a resolve would hand back
                // released objects.
                if text_input_02_990_window_is_live(state) {
                    unsafe { build_url_editor_window_run(base, owner) };
                }
            }
        }
    }
    if host_opens_profile_select() {
        // The native finalizer for a ProfileSelect window runs inside the original call above, so
        // this is the first moment its teardown is complete and a GFx call is safe again.
        let finalized = system_windows::take_finalized_profile_select();
        if finalized != 0
            && let Ok(base) = game_module_base()
        {
            unsafe {
                system_windows::restore_real_system_windows(
                    base,
                    "standalone-profile-finalized",
                    &SystemWindowHooks::NONE,
                )
            };
        }
        let filename = read_wide_resource_name(filename_ptr);
        unsafe { profile_select_window_run(job, filename.as_str()) };
    }
    // Menu-pump-owned: this is where a queued field is submitted and a finished one consumed.
    // Safety: this hook is the menu pump.
    unsafe { build_url_editor_menu_pump() };
    ret
}

/// Detour `MenuWindowJob::Run` so the link field has a menu pump.
///
/// Only a host that has no pump of its own calls this. The product does not: its own post-run work
/// already drives the field, from a detour it installs on the same address for the save picker and
/// the return-title chain as well.
///
/// # Safety
///
/// Process attach or startup-hook context.
pub unsafe fn install_quit_menu_window_run_hook() -> bool {
    if RUN_INSTALLED
        .compare_exchange(0, 1, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return true;
    }
    let Ok(addr) = game_rva_for_hook(MENU_WINDOW_JOB_RUN_RVA) else {
        append_autoload_debug(format_args!(
            "system-quit-build-url: failed to resolve MenuWindowJob::Run rva 0x{MENU_WINDOW_JOB_RUN_RVA:x}; the link field will open and then never be driven"
        ));
        RUN_INSTALLED.store(0, Ordering::SeqCst);
        return false;
    };
    match unsafe { er_hook::register_shared_hook(addr, quit_menu_window_job_run_hook, &RUN_ORIG) } {
        Ok(route) => {
            append_autoload_debug(format_args!(
                "system-quit-build-url: registered MenuWindowJob::Run 0x{addr:x} on the {route:?} union; the link field has a menu pump"
            ));
            true
        }
        Err(status) => {
            append_autoload_debug(format_args!(
                "system-quit-build-url: register_shared_hook MenuWindowJob::Run failed: {status:?}; the link field will open and then never be driven"
            ));
            RUN_INSTALLED.store(0, Ordering::SeqCst);
            false
        }
    }
}
