// DLL-drawn startup save-file picker overlay.
//
// Moved from `er-quickload/src/experiments/gpu_readback/save_picker_overlay.rs` as S4 of
// the save-picker extraction. Product/game state crosses through `host.rs`; raster primitives
// come from `er-loading-bar-core`, matching the boot bar's glyphs and rectangles.

// The keyboard hook, the OS input polling and the window geometry reads below are
// `#[cfg(windows)]`, so a host build compiles their helpers, key constants and imports with
// every caller cfg'd out. `dead_code` / `unused_imports` there describe the cfg, not real
// debt; the shipping target (x86_64-pc-windows-msvc) carries the full deny with no allows.
#![cfg_attr(not(windows), allow(dead_code, unused_imports))]

use std::sync::Mutex;
use std::sync::atomic::Ordering;

#[cfg(windows)]
use windows::Win32::{
    Foundation::{HWND, POINT, RECT},
    Graphics::Gdi::ScreenToClient,
    System::LibraryLoader::{GetModuleHandleA, GetProcAddress},
    UI::WindowsAndMessaging::{GetClientRect, GetCursorPos},
};
#[cfg(windows)]
use windows::core::PCSTR;

use crate::host::{
    MissingSaveSelectionOutcome, append_autoload_debug,
    complete_missing_save_selection_from_picker, game_main_window, missing_save_selection_pending,
    save_picker_seamless_mode_after_settle, save_picker_title_start_dir,
};
use crate::model::{self, PickerActivation, PickerStatusMessage, SavePickerModel};
use crate::slots::{SaveSlotInfo, parse_save_character_slots};

const BOOT_VIEW_GLYPH_ADV: usize = er_loading_bar_core::GLYPH_ADV;
const BOOT_VIEW_GLYPH_H: usize = er_loading_bar_core::GLYPH_H;

// The boot view's text and rects are the shared raster primitives -- these local names are
// aliases, not wrappers, so the picker and the boot bar cannot drift apart visually. Aliasing
// rather than forwarding also keeps the upstream arity out of this crate's own signatures.
use er_loading_bar_core::draw_text_rgb as boot_draw_text_rgb;
use er_loading_bar_core::fill_rect_rgb as boot_fill_rect;

/// Cached `user32!GetAsyncKeyState` / `xinput!XInputGetState` resolutions (0 = unresolved, !0 = tried-and-absent).
pub use er_telemetry_core::counters::GET_ASYNC_KEY_STATE_PROC;
/// 1 once the startup overlay picker has opened its model for this pending no-save boot. Distinct
/// from `SAVE_PICKER_MODE_ACTIVE` (the in-world System>Quit native-window picker).
pub use er_telemetry_core::counters::SAVE_PICKER_OVERLAY_ARMED;
pub use er_telemetry_core::counters::SAVE_PICKER_OVERLAY_DRAW_HITS;
pub use er_telemetry_core::counters::SAVE_PICKER_OVERLAY_HELD_POLLS;
pub use er_telemetry_core::counters::SAVE_PICKER_OVERLAY_INPUT_HITS;
/// Telemetry oracles.
pub use er_telemetry_core::counters::SAVE_PICKER_OVERLAY_OPEN_COUNT;
pub use er_telemetry_core::counters::SAVE_PICKER_OVERLAY_PICK_COUNT;
pub use er_telemetry_core::counters::SAVE_PICKER_OVERLAY_PICK_REJECT_COUNT;
/// Diagnostics for the "inputs eaten during load" report: total input polls the dedicated thread ran
/// (proves the thread is alive and at cadence, independent of the ~4 fps Present redraw), and polls
/// where any navigation key/button was down (proves the background thread can actually read OS input
/// under Wine/Proton -- if this stays ~0 while the user mashes, a background thread cannot see the
/// keys and input must move back to a pumped thread).
pub use er_telemetry_core::counters::SAVE_PICKER_OVERLAY_POLL_COUNT;
/// Previous frame's pressed-action bitmask (edge detection; see `PickerAction`).
pub use er_telemetry_core::counters::SAVE_PICKER_OVERLAY_PREV_ACTIONS;
pub use er_telemetry_core::counters::XINPUT_GET_STATE_PROC;

/// The autoload slot the character sub-picker chose (`usize::MAX` = none yet). The product-core
/// callsite reads this as the load target when no slot is configured.
pub use er_telemetry_core::counters::MISSING_SAVE_PICKER_SELECTED_SLOT;
/// Highlighted row in the character sub-picker.
pub use er_telemetry_core::counters::SAVE_PICKER_CHAR_CURSOR;
/// Overlay stage: 0 = browsing files, 1 = choosing a character (save slot) from the picked file.
pub use er_telemetry_core::counters::SAVE_PICKER_STAGE_CHARS;

/// The picked save awaiting a character selection: its path and the active character slots parsed
/// from its bytes.
struct PendingSave {
    path: std::path::PathBuf,
    slots: Vec<SaveSlotInfo>,
}
static SAVE_PICKER_PENDING_SAVE: Mutex<Option<PendingSave>> = Mutex::new(None);

fn pending_save_lock() -> std::sync::MutexGuard<'static, Option<PendingSave>> {
    SAVE_PICKER_PENDING_SAVE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// A character pick awaiting completion `(save path, slot)`. Set by the Present-hook input on the
/// render thread; consumed by [`save_picker_overlay_process_completion`] on the game-task thread so
/// the redirect activation + MinHook install runs off the render thread.
#[allow(clippy::type_complexity)]
static SAVE_PICKER_COMPLETE_REQUEST: Mutex<Option<(std::path::PathBuf, usize)>> = Mutex::new(None);

fn save_picker_complete_request_lock()
-> std::sync::MutexGuard<'static, Option<(std::path::PathBuf, usize)>> {
    SAVE_PICKER_COMPLETE_REQUEST
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Consume a pending character pick and complete it: activate the save redirect + install the
/// redirect hooks + release the save-check hold, so the autoload loads the chosen character. Call
/// from the game-task thread (safe for MinHook, and alive at pick time before loading starts).
/// No-op when no pick is pending.
pub fn save_picker_overlay_process_completion() {
    let request = save_picker_complete_request_lock().take();
    let Some((path, slot)) = request else {
        return;
    };
    commit_missing_save_selection(&path, slot, "picked");
}

/// Commit a save + character slot as this run's selection, without a user ever touching the picker.
///
/// The same commit the character sub-picker's own completion runs, exposed because the boot
/// autoload needs it for a save nobody has to choose. When the autoload's own row identifications
/// go unsatisfied, the save the run was configured with is still a perfectly good answer, and this
/// is the path that is measured to load it end to end: stage the source, release the save-check
/// hold, and let the game's own boot read take it from there (run br-20260913-030551-d8d9 did
/// exactly this and reached a live character, the only run of that session that did).
///
/// Returns whether the commit took. A refusal leaves the picker up carrying the reason, which is
/// the one case where the user does have to choose.
///
/// Call from the game-task thread: it activates the redirect and installs MinHook detours.
pub fn commit_missing_save_selection(path: &std::path::Path, slot: usize, origin: &str) -> bool {
    match complete_missing_save_selection_from_picker(path) {
        MissingSaveSelectionOutcome::Completed => {
            SAVE_PICKER_OVERLAY_PICK_COUNT.fetch_add(1, Ordering::SeqCst);
            MISSING_SAVE_PICKER_SELECTED_SLOT.store(slot, Ordering::SeqCst);
            append_autoload_debug(format_args!(
                "save-picker-overlay: completed {origin} '{}' slot {slot} -- redirect active, releasing the save-check hold to autoload that character",
                path.display()
            ));
            save_picker_overlay_disarm(origin);
            true
        }
        MissingSaveSelectionOutcome::Rejected(message) => {
            // Validation failed at commit: keep the character picker in place and name why, so the
            // rejection cannot be mistaken for Back/navigation.
            MISSING_SAVE_PICKER_SELECTED_SLOT.store(usize::MAX, Ordering::SeqCst);
            SAVE_PICKER_OVERLAY_PICK_REJECT_COUNT.fetch_add(1, Ordering::SeqCst);
            set_overlay_status_message(message);
            append_autoload_debug(format_args!(
                "save-picker-overlay: rejected completion for '{}' slot {slot} ({origin}); staying in character picker with visible reason",
                path.display()
            ));
            false
        }
    }
}

/// The character sub-picker's chosen autoload slot, if one has been picked this session.
pub fn missing_save_picker_selected_slot() -> Option<i32> {
    let v = MISSING_SAVE_PICKER_SELECTED_SLOT.load(Ordering::SeqCst);
    (v != usize::MAX).then_some(v as i32)
}

const PROC_ABSENT: usize = usize::MAX;

// Edge-triggered logical actions (one step per press).
const PICKER_ACT_UP: usize = 1 << 0;
const PICKER_ACT_DOWN: usize = 1 << 1;
const PICKER_ACT_LEFT: usize = 1 << 2;
const PICKER_ACT_RIGHT: usize = 1 << 3;
const PICKER_ACT_SELECT: usize = 1 << 4;
const PICKER_ACT_BACK: usize = 1 << 5;

// Virtual-key codes (win32).
const VK_LBUTTON: i32 = 0x01;
const VK_RBUTTON: i32 = 0x02;
const VK_BACK: i32 = 0x08;
const VK_RETURN: i32 = 0x0d;
const VK_LEFT: i32 = 0x25;
const VK_UP: i32 = 0x26;
const VK_RIGHT: i32 = 0x27;
const VK_DOWN: i32 = 0x28;

// XInput gamepad button bits.
const XINPUT_DPAD_UP: u16 = 0x0001;
const XINPUT_DPAD_DOWN: u16 = 0x0002;
const XINPUT_DPAD_LEFT: u16 = 0x0004;
const XINPUT_DPAD_RIGHT: u16 = 0x0008;
const XINPUT_A: u16 = 0x1000;
const XINPUT_B: u16 = 0x2000;

/// True while the DLL-drawn startup picker owns the screen (draw + input). Gated on the same
/// missing-save-pending latch that holds the boot.
pub fn save_picker_overlay_active() -> bool {
    SAVE_PICKER_OVERLAY_ARMED.load(Ordering::SeqCst) != 0 && missing_save_selection_pending()
}

type GetAsyncKeyStateFn = unsafe extern "system" fn(i32) -> i16;
type XInputGetStateFn = unsafe extern "system" fn(u32, *mut XInputStateRaw) -> u32;

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

#[cfg(windows)]
fn resolve_get_async_key_state() -> Option<GetAsyncKeyStateFn> {
    let cached = GET_ASYNC_KEY_STATE_PROC.load(Ordering::SeqCst);
    if cached == PROC_ABSENT {
        return None;
    }
    if cached != 0 {
        return Some(unsafe { std::mem::transmute::<usize, GetAsyncKeyStateFn>(cached) });
    }
    let addr = unsafe { GetModuleHandleA(PCSTR(c"user32.dll".as_ptr().cast::<u8>())) }
        .ok()
        .and_then(|m| unsafe {
            GetProcAddress(m, PCSTR(c"GetAsyncKeyState".as_ptr().cast::<u8>()))
        })
        .map(|p| p as usize);
    match addr {
        Some(a) if a != 0 => {
            GET_ASYNC_KEY_STATE_PROC.store(a, Ordering::SeqCst);
            Some(unsafe { std::mem::transmute::<usize, GetAsyncKeyStateFn>(a) })
        }
        _ => {
            GET_ASYNC_KEY_STATE_PROC.store(PROC_ABSENT, Ordering::SeqCst);
            None
        }
    }
}

#[cfg(not(windows))]
fn resolve_get_async_key_state() -> Option<GetAsyncKeyStateFn> {
    None
}

#[cfg(windows)]
fn resolve_xinput_get_state() -> Option<XInputGetStateFn> {
    let cached = XINPUT_GET_STATE_PROC.load(Ordering::SeqCst);
    if cached == PROC_ABSENT {
        return None;
    }
    if cached != 0 {
        return Some(unsafe { std::mem::transmute::<usize, XInputGetStateFn>(cached) });
    }
    // The game loads XInput for its own gamepad support, so GetModuleHandleA resolves it without a
    // LoadLibrary; if absent (keyboard-only session), gamepad nav is simply unavailable.
    for dll in [
        b"xinput1_4.dll\0".as_slice(),
        b"xinput1_3.dll\0",
        b"xinput9_1_0.dll\0",
    ] {
        let Ok(module) = (unsafe { GetModuleHandleA(PCSTR(dll.as_ptr())) }) else {
            continue;
        };
        if let Some(proc) =
            unsafe { GetProcAddress(module, PCSTR(c"XInputGetState".as_ptr().cast::<u8>())) }
        {
            let a = proc as usize;
            XINPUT_GET_STATE_PROC.store(a, Ordering::SeqCst);
            return Some(unsafe { std::mem::transmute::<usize, XInputGetStateFn>(a) });
        }
    }
    XINPUT_GET_STATE_PROC.store(PROC_ABSENT, Ordering::SeqCst);
    None
}

#[cfg(not(windows))]
fn resolve_xinput_get_state() -> Option<XInputGetStateFn> {
    None
}

/// Sample keyboard + gamepad. Returns `(held_now, pressed_this_poll)`.
///
/// Keyboard "pressed" uses the low bit of `GetAsyncKeyState` ("pressed since our previous call"), so
/// a press is caught even when it happened and was released between two of the slow (~4 fps)
/// boot-frame polls -- polling only the high bit drops those, which is why deliberate navigation felt
/// eaten. Gamepad has no such bit, so it edge-detects the button state vs the previous poll.
///
/// Must be called on the game's render thread (the Present hook). `GetAsyncKeyState` does not report
/// the user's keys from a background thread under Wine/Proton -- measured: a dedicated poll thread ran
/// 1089 polls yet saw only 5 key-downs while the user mashed, and completed 0 picks.
fn save_picker_sample() -> (usize, usize) {
    let mut held = 0usize;
    let mut pressed = 0usize;
    // Keyboard: only when the event-driven low-level hook is not active. The hook (when installed)
    // owns keyboard so every press registers regardless of this poll's ~4fps boot rate; polling it
    // here too would double-apply. This branch is the fallback if the hook failed to install.
    if !SAVE_PICKER_KBD_HOOK_ACTIVE.load(Ordering::SeqCst)
        && let Some(gaks) = resolve_get_async_key_state()
    {
        let mut probe = |vk: i32, act: usize| {
            let state = unsafe { gaks(vk) } as u16;
            if state & 0x8000 != 0 {
                held |= act; // currently down
            }
            if state & 0x0001 != 0 {
                pressed |= act; // pressed since our previous poll
            }
        };
        probe(VK_UP, PICKER_ACT_UP);
        probe(VK_DOWN, PICKER_ACT_DOWN);
        probe(VK_LEFT, PICKER_ACT_LEFT);
        probe(VK_RIGHT, PICKER_ACT_RIGHT);
        probe(VK_RETURN, PICKER_ACT_SELECT);
        probe(VK_BACK, PICKER_ACT_BACK);
    }
    if let Some(xinput) = resolve_xinput_get_state() {
        let mut st = XInputStateRaw::default();
        // Only controller 0; ERROR_SUCCESS(0) == connected.
        if unsafe { xinput(0, &mut st) } == 0 {
            let b = st.gamepad.buttons;
            let mut gamepad = 0usize;
            if b & XINPUT_DPAD_UP != 0 {
                gamepad |= PICKER_ACT_UP;
            }
            if b & XINPUT_DPAD_DOWN != 0 {
                gamepad |= PICKER_ACT_DOWN;
            }
            if b & XINPUT_DPAD_LEFT != 0 {
                gamepad |= PICKER_ACT_LEFT;
            }
            if b & XINPUT_DPAD_RIGHT != 0 {
                gamepad |= PICKER_ACT_RIGHT;
            }
            if b & XINPUT_A != 0 {
                gamepad |= PICKER_ACT_SELECT;
            }
            if b & XINPUT_B != 0 {
                gamepad |= PICKER_ACT_BACK;
            }
            held |= gamepad;
            let prev = SAVE_PICKER_OVERLAY_PREV_ACTIONS.swap(gamepad, Ordering::SeqCst);
            pressed |= gamepad & !prev; // rising edges only
        }
    }
    (held, pressed)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum MouseButton {
    Left,
    Right,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct MouseClick {
    x: usize,
    y: usize,
    w: usize,
    h: usize,
    button: MouseButton,
}

#[cfg(windows)]
fn save_picker_mouse_click() -> Option<MouseClick> {
    let gaks = resolve_get_async_key_state()?;
    let left = unsafe { gaks(VK_LBUTTON) } as u16;
    let right = unsafe { gaks(VK_RBUTTON) } as u16;
    let button = if left & 0x0001 != 0 {
        MouseButton::Left
    } else if right & 0x0001 != 0 {
        MouseButton::Right
    } else {
        return None;
    };
    let hwnd_raw = game_main_window();
    if hwnd_raw == 0 {
        return None;
    }
    let hwnd = HWND(hwnd_raw as *mut core::ffi::c_void);
    let mut point = POINT::default();
    if unsafe { GetCursorPos(&mut point) }.is_err() {
        return None;
    }
    if !unsafe { ScreenToClient(hwnd, &mut point) }.as_bool() {
        return None;
    }
    let mut rect = RECT::default();
    if unsafe { GetClientRect(hwnd, &mut rect) }.is_err() {
        return None;
    }
    let w = (rect.right - rect.left).max(0) as usize;
    let h = (rect.bottom - rect.top).max(0) as usize;
    if w == 0 || h == 0 || point.x < 0 || point.y < 0 {
        return None;
    }
    let x = point.x as usize;
    let y = point.y as usize;
    (x < w && y < h).then_some(MouseClick { x, y, w, h, button })
}

#[cfg(not(windows))]
fn save_picker_mouse_click() -> Option<MouseClick> {
    None
}

/// The in-game arm of the missing-save boot picker: open the overlay's model for the pending
/// no-save boot if not already armed. Idempotent, and safe from any thread (Mutex state plus a
/// directory enumeration; it touches no game pointer).
///
/// Reached through [`open_picker_for_intent`] like every other picker open, so
/// `os_native_save_picker` decides between this and the OS dialog in one place. It is also the
/// fallback the OS arm hands the pick to when comdlg32 cannot be used, which is why it is callable
/// on its own rather than only from the router.
///
/// Returns whether the boot pick is now owned by this surface.
pub fn arm_boot_picker() -> bool {
    save_picker_overlay_arm_if_pending();
    save_picker_overlay_active()
}

/// Stage an already-validated container into the character sub-picker, arming the overlay's file
/// browser at that container's own folder so the sub-picker's back lands somewhere real.
///
/// Exists for the OS boot arm: comdlg32 chooses a file and has no character list, but
/// `native_fullread_slot()` needs the slot the sub-picker records or it falls through to slot 0 and
/// the save watchdog aborts on a container whose slot 0 is empty. This is the same tail the
/// in-game file stage runs after its own pick -- deliberately the same code path, so the two
/// surfaces cannot diverge on what choosing a character means.
///
/// `false` when the container yields no readable character slots, which the caller must treat as
/// "this pick cannot proceed" rather than staging a sub-picker with nothing in it.
pub fn boot_stage_picked_save_for_character_choice(path: std::path::PathBuf) -> bool {
    let slots = std::fs::read(&path)
        .ok()
        .map(|bytes| parse_save_character_slots(&bytes))
        .unwrap_or_default();
    if slots.is_empty() {
        SAVE_PICKER_OVERLAY_PICK_REJECT_COUNT.fetch_add(1, Ordering::SeqCst);
        return false;
    }
    // Arm the browser at the picked file's folder before switching stages, so a back out of the
    // character list finds a populated listing instead of an empty panel.
    if let Some(parent) = path.parent() {
        save_picker_overlay_arm_at(parent);
    }
    append_autoload_debug(format_args!(
        "save-picker-overlay: staged '{}' from the OS dialog -- {} character slots; opening character sub-picker",
        path.display(),
        slots.len()
    ));
    *pending_save_lock() = Some(PendingSave { path, slots });
    SAVE_PICKER_CHAR_CURSOR.store(0, Ordering::SeqCst);
    SAVE_PICKER_STAGE_CHARS.store(1, Ordering::SeqCst);
    true
}

/// Force the boot picker surface down when this DLL stops owning the pending pick.
///
/// This is intentionally narrower than a product save completion: it only drops this crate's
/// model/input state. The host still owns whatever latch made [`save_picker_overlay_active`]
/// true in the first place.
pub fn stand_down_boot_picker(reason: &str) {
    save_picker_overlay_disarm(reason);
}

/// Open the picker model for the pending no-save boot if not already armed. Idempotent.
fn save_picker_overlay_arm_if_pending() {
    let start_dir = save_picker_title_start_dir();
    save_picker_overlay_arm_at(&start_dir);
}

/// Arm the overlay's browser rooted at `start_dir`, if a missing-save pick is pending and nothing
/// is armed yet. Idempotent.
fn save_picker_overlay_arm_at(start_dir: &std::path::Path) {
    if !missing_save_selection_pending() || SAVE_PICKER_OVERLAY_ARMED.load(Ordering::SeqCst) != 0 {
        return;
    }
    let seamless = save_picker_seamless_mode_after_settle("startup-overlay-picker");
    let model = if seamless {
        SavePickerModel::open_with_extensions(start_dir, &["co2", "sl2"])
    } else {
        SavePickerModel::open(start_dir, "sl2")
    };
    *model::active_save_picker_lock() = Some(model);
    // Seed the banner from the reason the arming site recorded, so the browser says what went
    // wrong instead of appearing over the title with no explanation. `Unrecorded` still gets
    // copy -- "could not start a character, and did not record why" is the honest sentence for a
    // picker nobody claimed, and its counter makes that case a number rather than a silence.
    set_overlay_status_message(crate::reason::missing_save_banner());
    SAVE_PICKER_OVERLAY_ARMED.store(1, Ordering::SeqCst);
    SAVE_PICKER_OVERLAY_OPEN_COUNT.fetch_add(1, Ordering::SeqCst);
    SAVE_PICKER_OVERLAY_PREV_ACTIONS.store(0, Ordering::SeqCst);
    let reason = crate::reason::missing_save_reason();
    append_autoload_debug(format_args!(
        "save-picker-overlay: opened DLL-drawn startup picker reason={} fault={:?} banner='{}: {}' dir='{}' ext=.{}",
        reason.log_tag(),
        reason.fault(),
        reason.banner().headline(),
        reason.banner().detail(),
        start_dir.display(),
        model::active_save_picker_lock()
            .as_ref()
            .map(|model| model.extension().to_owned())
            .unwrap_or_else(|| "<unset>".to_owned())
    ));
}

/// Disarm the overlay (pick completed / no longer pending): drop the model and reset edge state.
fn set_overlay_status_message(message: PickerStatusMessage) {
    if let Some(model) = model::active_save_picker_lock().as_mut() {
        model.set_status_message(message);
    }
}

fn save_picker_overlay_status_message() -> Option<PickerStatusMessage> {
    model::active_save_picker_lock()
        .as_ref()
        .and_then(|model| model.status_message().cloned())
}

fn save_picker_overlay_disarm(reason: &str) {
    if SAVE_PICKER_OVERLAY_ARMED.swap(0, Ordering::SeqCst) == 0 {
        return;
    }
    // The startup overlay and the in-world System>Quit picker are mutually exclusive (startup
    // resolves before the world is reachable), so the overlay owns the shared model slot.
    *model::active_save_picker_lock() = None;
    SAVE_PICKER_OVERLAY_PREV_ACTIONS.store(0, Ordering::SeqCst);
    // Reset the character sub-picker stage (the chosen slot in MISSING_SAVE_PICKER_SELECTED_SLOT is
    // intentionally left set -- the autoload callsite still needs it to load the picked character).
    SAVE_PICKER_STAGE_CHARS.store(0, Ordering::SeqCst);
    SAVE_PICKER_CHAR_CURSOR.store(0, Ordering::SeqCst);
    *pending_save_lock() = None;
    append_autoload_debug(format_args!("save-picker-overlay: disarmed ({reason})"));
}

/// Lines the status banner occupies when there is one: a headline plus up to
/// [`PICKER_STATUS_DETAIL_LINES`] wrapped lines of detail.
///
/// Reserved whether or not the detail wraps that far, because the row geometry below it has to be
/// the same number for the rasteriser and for the mouse hit test. A banner that shifted the rows
/// by however many lines it happened to wrap to would put the click one row off its label.
const PICKER_STATUS_DETAIL_LINES: usize = 2;
const PICKER_STATUS_LINES: usize = 1 + PICKER_STATUS_DETAIL_LINES;

/// Most rows the overlay will draw, however tall the frame is.
///
/// The native `05_010` window is fixed at ten slots; this surface rasterises its own rows and is
/// bounded only by the panel. Thirty is where a directory listing stops being a list and starts
/// being a wall, and it is what the user asked for.
const PICKER_OVERLAY_ROW_MAX: usize = 30;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PickerOverlayMetrics {
    /// Panel left edge.
    margin_x: usize,
    /// Panel width.
    content_w: usize,
    panel_top: usize,
    panel_bottom: usize,
    /// Body text scale, derived from the frame height rather than fixed at 2: at 1080p a
    /// scale-2 glyph is ten pixels wide and the user reported the picker as unreadably small.
    scale: usize,
    /// Heading scale. One step above the body, which is the whole of the type hierarchy here --
    /// this font has one weight, so size and colour are the only levers.
    title_scale: usize,
    line_h: usize,
    title_h: usize,
    /// Inner padding between the panel edge and its content. Previously `scale * 4`, eight
    /// pixels, which read as no margin at all.
    pad: usize,
    /// Left edge of text, i.e. the panel edge plus the padding.
    text_x: usize,
    /// Width available to text inside the padding.
    text_w: usize,
    /// Space between one block of text and the next.
    gap: usize,
    /// Thickness of a divider rule.
    rule_h: usize,
    row_step: usize,
}

fn picker_overlay_metrics(w: usize, h: usize) -> Option<PickerOverlayMetrics> {
    if w == 0 || h == 0 {
        return None;
    }
    // The boot bar's own rule, not a second one: the picker's rows and the bar's label are drawn
    // on the same frame, and the user compared them directly -- the picker was half again the
    // bar's height. Body text is now exactly the bar's size and the heading is one step above it,
    // which is the whole type hierarchy this font can express.
    let scale = er_loading_bar_core::boot_text_scale(h);
    let title_scale = scale + 1;
    let line_h = BOOT_VIEW_GLYPH_H * scale;
    let title_h = BOOT_VIEW_GLYPH_H * title_scale;
    let margin_x = (w / 12).max(24);
    let content_w = w.checked_sub(margin_x * 2)?;
    let panel_top = (h / 16).max(24);
    let panel_bottom = h * 88 / 100;
    let pad = line_h;
    if content_w <= pad * 2 || panel_bottom <= panel_top || line_h == 0 {
        return None;
    }
    Some(PickerOverlayMetrics {
        margin_x,
        content_w,
        panel_top,
        panel_bottom,
        scale,
        title_scale,
        line_h,
        title_h,
        pad,
        text_x: margin_x + pad,
        text_w: content_w - pad * 2,
        gap: line_h,
        rule_h: scale.max(1),
        // 1.33 lines rather than 1.5: the rows are the content, and the tighter step is what buys
        // the extra rows the taller type would otherwise cost.
        row_step: line_h + line_h / 3,
    })
}

impl PickerOverlayMetrics {
    /// First pixel row below the header block, given how many header text lines sit under the
    /// title. One implementation for both stages so a header change cannot move the rows for the
    /// rasteriser without moving them for the hit test.
    fn rows_start_y(self, info_lines: usize, has_status: bool) -> usize {
        // Block by block, each followed by its own gap. Before this the header was a run of lines
        // with nothing between them, so the path, the filter line and the warning read as one
        // paragraph -- which is how a warning ends up looking like a subtitle.
        self.panel_top
            + self.pad
            + self.title_h
            + self.gap
            + self.rule_h
            + self.gap
            + info_lines * self.line_h
            + self.gap
            + usize::from(has_status) * (PICKER_STATUS_LINES * self.line_h + self.gap)
            + self.rule_h
            + self.gap
    }

    /// Bottom limit for rows: the footer hint plus a line of breathing room.
    fn rows_bottom_y(self) -> usize {
        self.panel_bottom.saturating_sub(self.line_h * 2 + self.pad)
    }

    /// How many rows fit between `rows_start_y` and the footer.
    fn rows_visible(self, info_lines: usize, has_status: bool) -> usize {
        let start = self.rows_start_y(info_lines, has_status);
        self.rows_bottom_y()
            .saturating_sub(start)
            .checked_div(self.row_step)
            .unwrap_or(0)
            .clamp(1, PICKER_OVERLAY_ROW_MAX)
    }
}

/// Header lines under the file browser's title when the drive row is carrying the path itself:
/// the filter/count line, and nothing else.
const PICKER_FILE_INFO_LINES: usize = 1;

/// Header lines for this model: one more when there is no drive row, because then nothing else on
/// screen says which folder is being browsed.
///
/// The path was in the header and also inline on row 0 once the drive strip gained its field,
/// which is
/// the same string twice on one panel.
fn picker_file_info_lines(model: &SavePickerModel) -> usize {
    PICKER_FILE_INFO_LINES + usize::from(model.drive_row().is_none())
}
/// Header lines under the character stage's title: the file the characters came from.
const PICKER_CHARACTER_INFO_LINES: usize = 1;

fn picker_file_rows_start_y(
    metrics: PickerOverlayMetrics,
    info_lines: usize,
    has_status: bool,
) -> usize {
    metrics.rows_start_y(info_lines, has_status)
}

fn picker_character_rows_start_y(metrics: PickerOverlayMetrics, has_status: bool) -> usize {
    metrics.rows_start_y(PICKER_CHARACTER_INFO_LINES, has_status)
}

fn picker_row_hit(
    metrics: PickerOverlayMetrics,
    x: usize,
    y: usize,
    first_row_y: usize,
    rows: usize,
) -> Option<usize> {
    let x0 = metrics.margin_x + metrics.pad / 2;
    let x1 = metrics
        .margin_x
        .checked_add(metrics.content_w.saturating_sub(metrics.pad / 2))?;
    if x < x0 || x >= x1 {
        return None;
    }
    let rows_bottom = metrics.rows_bottom_y();
    for row in 0..rows {
        let row_y = first_row_y + row * metrics.row_step;
        if row_y + metrics.line_h >= rows_bottom {
            break;
        }
        let y0 = row_y.saturating_sub(metrics.scale * 2);
        let y1 = row_y + metrics.line_h + metrics.scale * 2;
        if y >= y0 && y < y1 {
            return Some(row);
        }
    }
    None
}

fn picker_file_row_hit(model: &SavePickerModel, click: MouseClick) -> Option<usize> {
    let metrics = picker_overlay_metrics(click.w, click.h)?;
    let first_row_y = picker_file_rows_start_y(
        metrics,
        picker_file_info_lines(model),
        model.status_message().is_some(),
    );
    let row = picker_row_hit(metrics, click.x, click.y, first_row_y, model.row_capacity())?;
    (!model.row_label_ascii(row).is_empty()).then_some(row)
}

fn picker_character_row_hit(
    click: MouseClick,
    slot_count: usize,
    has_status: bool,
) -> Option<usize> {
    let metrics = picker_overlay_metrics(click.w, click.h)?;
    let first_row_y = picker_character_rows_start_y(metrics, has_status);
    picker_row_hit(metrics, click.x, click.y, first_row_y, slot_count)
}

/// One input poll for the startup overlay picker. Reads OS keyboard/gamepad directly (independent of
/// the game's blocked input) and captures presses. Must run on the game's render thread -- it is
/// driven from the D3D12 Present hook, which is the only thread that can read `GetAsyncKeyState`
/// under Wine/Proton. Present starves to ~4 fps while the boot streams assets, so a press could fall
/// between two polls; [`save_picker_sample`] uses the GetAsyncKeyState "pressed-since-last-call" bit
/// so those presses are still caught (that dropping was the "inputs eaten" symptom). Navigation is
/// applied here (pure Mutex state); the one-shot pick completion (redirect + MinHook install) is
/// deferred to [`save_picker_overlay_process_completion`] on the game-task thread. No-op unless the
/// overlay is active.
pub fn save_picker_overlay_input_tick() {
    // The host owns the open decision by calling `arm_boot_picker()` only after its surface router
    // decides this overlay owns the boot pick. This tick only drives/drops an already-armed overlay;
    // arming here would bypass the OS-native boot surface again.
    if !save_picker_overlay_active() {
        // No longer pending -> the pick released the hold; drop the model.
        save_picker_overlay_disarm("not-pending");
        return;
    }
    SAVE_PICKER_OVERLAY_POLL_COUNT.fetch_add(1, Ordering::SeqCst);
    let (held, pressed) = save_picker_sample();
    if held != 0 {
        SAVE_PICKER_OVERLAY_HELD_POLLS.fetch_add(1, Ordering::SeqCst);
    }
    if pressed != 0 {
        save_picker_apply_pressed(pressed);
    }
    if let Some(click) = save_picker_mouse_click() {
        save_picker_apply_mouse_click(click);
    }
}

/// Apply one pressed-action bitmask to the active picker stage. Shared by the render-thread gamepad
/// poll and the low-level keyboard hook so both funnel through the same dispatch.
fn save_picker_apply_pressed(pressed: usize) {
    if pressed == 0 {
        return;
    }
    let hits = SAVE_PICKER_OVERLAY_INPUT_HITS.fetch_add(1, Ordering::SeqCst) + 1;
    let chars_stage = SAVE_PICKER_STAGE_CHARS.load(Ordering::SeqCst) != 0;
    // Log every applied action so the exact input sequence + timing is visible in the debug log
    // (distinguishes genuine under-registering from a slow redraw that just hides the feedback).
    append_autoload_debug(format_args!(
        "save-picker-input: applied #{hits} action=0x{pressed:x} stage={} src_hook={}",
        if chars_stage { "chars" } else { "files" },
        SAVE_PICKER_KBD_HOOK_ACTIVE.load(Ordering::SeqCst)
    ));
    if save_picker_path_edit_active() {
        // Every key belongs to the field while it is open; the hook routes them there directly.
        // Back is the one that still means something here: it leaves the field.
        if pressed & PICKER_ACT_BACK != 0 {
            path_edit_cancel();
        }
        return;
    }
    if chars_stage {
        save_picker_character_stage_input(pressed);
    } else {
        save_picker_file_stage_input(pressed);
    }
}

/// Map a Win32 virtual-key to a picker action, or 0 if it is not a nav key.
fn picker_action_for_vk(vk: i32) -> usize {
    match vk {
        VK_UP => PICKER_ACT_UP,
        VK_DOWN => PICKER_ACT_DOWN,
        VK_LEFT => PICKER_ACT_LEFT,
        VK_RIGHT => PICKER_ACT_RIGHT,
        VK_RETURN => PICKER_ACT_SELECT,
        VK_BACK => PICKER_ACT_BACK,
        _ => 0,
    }
}

/// Apply one left-click to whichever picker stage is active. A click on a visible file-browser row
/// both moves the highlight to that row and activates it, so the mouse can use every row action the
/// keyboard/controller path can: up-directory, drive cycle, page cycle, directory open and save-file
/// selection. In the character sub-picker a click chooses the clicked slot directly.
fn save_picker_apply_mouse_click(click: MouseClick) {
    let hits = SAVE_PICKER_OVERLAY_INPUT_HITS.fetch_add(1, Ordering::SeqCst) + 1;
    let chars_stage = SAVE_PICKER_STAGE_CHARS.load(Ordering::SeqCst) != 0;
    append_autoload_debug(format_args!(
        "save-picker-input: applied #{hits} mouse {:?} stage={} at {},{} client={}x{}",
        click.button,
        if chars_stage { "chars" } else { "files" },
        click.x,
        click.y,
        click.w,
        click.h
    ));
    if chars_stage {
        save_picker_character_stage_mouse_click(click);
    } else {
        save_picker_file_stage_mouse_click(click);
    }
}

/// True while the WH_KEYBOARD_LL hook is installed and pumping; the render-thread poll then skips
/// keyboard (the hook owns it) and does gamepad only.
static SAVE_PICKER_KBD_HOOK_ACTIVE: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
/// One-shot spawn guard for the hook thread.
static SAVE_PICKER_KBD_HOOK_STARTED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
/// Telemetry: key-down events the ll hook applied (proves event-driven capture fires under Wine).
pub use er_telemetry_core::counters::SAVE_PICKER_KBD_HOOK_HITS;

/// WH_KEYBOARD_LL callback: every keystroke arrives here as an OS event, independent of the game's
/// ~4fps boot Present/task rate, so no press is lost or collapsed. Applies one action per physical
/// press (auto-repeat suppressed via the down-mask). Never blocks the game's own input -- always
/// chains via CallNextHookEx.
#[cfg(windows)]
unsafe extern "system" fn save_picker_ll_keyboard_proc(
    ncode: i32,
    wparam: windows::Win32::Foundation::WPARAM,
    lparam: windows::Win32::Foundation::LPARAM,
) -> windows::Win32::Foundation::LRESULT {
    use windows::Win32::UI::WindowsAndMessaging::{
        CallNextHookEx, HC_ACTION, KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_SYSKEYDOWN,
    };
    if ncode == HC_ACTION as i32 && lparam.0 != 0 {
        let kb = unsafe { &*(lparam.0 as *const KBDLLHOOKSTRUCT) };
        let msg = wparam.0 as u32;
        // The path field owns the keyboard while it is open, so a folder name containing `u`,
        // `d`, or a digit cannot double as cursor navigation.
        if (msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN)
            && save_picker_path_edit_active()
            && save_picker_overlay_active()
        {
            let shift = resolve_get_async_key_state()
                .map(|gaks| (unsafe { gaks(VK_SHIFT) } as u16) & 0x8000 != 0)
                .unwrap_or(false);
            SAVE_PICKER_KBD_HOOK_HITS.fetch_add(1, Ordering::SeqCst);
            let vk = kb.vkCode as i32;
            let _ = std::panic::catch_unwind(|| save_picker_path_edit_key(vk, shift));
            return unsafe { CallNextHookEx(None, ncode, wparam, lparam) };
        }
        let act = picker_action_for_vk(kb.vkCode as i32);
        // Apply on every key-down (including OS auto-repeat). A held-key guard that cleared only on
        // key-up swallowed the user's repeated taps whenever the up event was missed (measured: 10
        // edges for many more presses). A distinct tap is a single KEYDOWN, so tapping stays 1:1; only
        // holding a key repeats, which is acceptable for a picker.
        if act != 0 && (msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN) {
            SAVE_PICKER_KBD_HOOK_HITS.fetch_add(1, Ordering::SeqCst);
            if save_picker_overlay_active() {
                let _ = std::panic::catch_unwind(|| save_picker_apply_pressed(act));
            }
        }
    }
    unsafe { CallNextHookEx(None, ncode, wparam, lparam) }
}

/// Spawn the picker's low-level keyboard hook + message pump once while a missing-save pick is
/// pending. Uninstalls and exits once the pick resolves. Falls back to the render-thread poll if the
/// hook fails to install.
#[cfg(windows)]
pub fn ensure_save_picker_keyboard_hook() {
    if !missing_save_selection_pending() {
        return;
    }
    if SAVE_PICKER_KBD_HOOK_STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    let spawned = std::thread::Builder::new()
        .name("er-save-picker-kbd".into())
        .spawn(|| {
            use windows::Win32::Foundation::HWND;
            use windows::Win32::System::Threading::{
                GetCurrentThread, SetThreadPriority, THREAD_PRIORITY_TIME_CRITICAL,
            };
            use windows::Win32::UI::WindowsAndMessaging::{
                DispatchMessageW, MSG, MWMO_INPUTAVAILABLE, MsgWaitForMultipleObjectsEx, PM_REMOVE,
                PeekMessageW, QS_ALLINPUT, SetWindowsHookExW, TranslateMessage,
                UnhookWindowsHookEx, WH_KEYBOARD_LL,
            };
            // Keep this thread scheduled through the heavy boot load. A low-level keyboard hook whose
            // thread isn't serviced within ~300ms gets bypassed by the OS -- that dropped keypress is
            // the "loading eats my inputs" symptom. The thread parks in the message wait, so top
            // priority never costs CPU.
            unsafe {
                let _ = SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL);
            }
            let Ok(hook) = (unsafe {
                SetWindowsHookExW(WH_KEYBOARD_LL, Some(save_picker_ll_keyboard_proc), None, 0)
            }) else {
                SAVE_PICKER_KBD_HOOK_STARTED.store(false, Ordering::SeqCst);
                return;
            };
            SAVE_PICKER_KBD_HOOK_ACTIVE.store(true, Ordering::SeqCst);
            let mut msg = MSG::default();
            const POLL_MS: u32 = 50;
            loop {
                if !missing_save_selection_pending() && !save_picker_overlay_active() {
                    break;
                }
                // Bounded OS message wait (~50ms): the ll hook callback fires during it; then drain
                // the queue so the pump stays alive. Not a sleep -- a message wait with a wake mask.
                let _ = unsafe {
                    MsgWaitForMultipleObjectsEx(None, POLL_MS, QS_ALLINPUT, MWMO_INPUTAVAILABLE)
                };
                while unsafe {
                    PeekMessageW(&mut msg, Some(HWND(std::ptr::null_mut())), 0, 0, PM_REMOVE)
                }
                .as_bool()
                {
                    unsafe {
                        let _ = TranslateMessage(&msg);
                        DispatchMessageW(&msg);
                    }
                }
            }
            unsafe {
                let _ = UnhookWindowsHookEx(hook);
            }
            SAVE_PICKER_KBD_HOOK_ACTIVE.store(false, Ordering::SeqCst);
            SAVE_PICKER_KBD_HOOK_STARTED.store(false, Ordering::SeqCst);
        });
    if spawned.is_err() {
        SAVE_PICKER_KBD_HOOK_STARTED.store(false, Ordering::SeqCst);
    }
}

/// Host builds have no keyboard hook to install. Kept beside the windows implementation, not
/// at the end of the file: a `cfg`-gated item after the test module is invisible on the
/// shipping target and only shows up as `items_after_test_module` on the host.
#[cfg(not(windows))]
pub fn ensure_save_picker_keyboard_hook() {}

/// File-browser stage input: navigate/drive/page, and on picking a save file, parse its character
/// slots and switch to the character sub-picker (the redirect + load are deferred until a
/// character is chosen).
fn save_picker_file_stage_input(pressed: usize) {
    // Set when select resolved to something that is not a pickable file. That path used to
    // return silently -- no log, no status line, no counter -- so confirming on the drive-selector
    // row or a directory produced literally no feedback anywhere. It cost a live run to
    // diagnose: three SELECTs applied, nothing happened, and nothing said why.
    let mut ignored_select: Option<(&'static str, usize)> = None;
    let picked = {
        let mut guard = model::active_save_picker_lock();
        let Some(model) = guard.as_mut() else {
            return;
        };
        if pressed & PICKER_ACT_UP != 0 {
            model.move_cursor(false);
        }
        if pressed & PICKER_ACT_DOWN != 0 {
            model.move_cursor(true);
        }
        // Left/right walk the drive strip when the highlight is on row 0, else page the listing.
        // `cycle_drive_from_drive_strip` is the native surface's own step: walking right off the
        // last drive lands on the complete-path field rather than wrapping, which is what makes
        // the field reachable without a mouse.
        if pressed & PICKER_ACT_LEFT != 0 {
            if model.cursor_on_drive_selector() {
                model.cycle_drive_from_drive_strip(false);
            } else {
                model.cycle_page(false);
            }
        }
        if pressed & PICKER_ACT_RIGHT != 0 {
            if model.cursor_on_drive_selector() {
                model.cycle_drive_from_drive_strip(true);
            } else {
                model.cycle_page(true);
            }
        }
        if pressed & PICKER_ACT_BACK != 0 {
            model.go_up();
        }
        // Confirming on row 0 means one of two things, and the strip's focus says which: a drive
        // cell switches drive, the path field starts an edit seeded with the current folder.
        if pressed & PICKER_ACT_SELECT != 0 && model.cursor_on_drive_selector() {
            match model.drive_strip_focus() {
                Some(model::DriveStripFocus::CurrentPath) => {
                    let seed = model.current_dir().display().to_string();
                    model.begin_path_edit();
                    path_edit_begin(seed);
                    append_autoload_debug(format_args!(
                        "save-picker-overlay: path field editing started -- type a folder, TAB completes, ENTER opens it, ESC cancels"
                    ));
                    return;
                }
                Some(model::DriveStripFocus::Cell(cell)) => {
                    model.activate_drive_strip_cell(cell);
                    return;
                }
                None => {}
            }
        }
        if pressed & PICKER_ACT_SELECT != 0 {
            let cursor_row = model.cursor();
            match model.activate_cursor() {
                PickerActivation::PickedFile(path) => Some(path),
                PickerActivation::PickedNewFile(_) => {
                    ignored_select = Some(("new-file row (destination intent)", cursor_row));
                    None
                }
                PickerActivation::Repopulate => {
                    // A directory / drive change. Legitimate navigation, but worth naming so a
                    // driver can tell "I moved into a folder" from "nothing happened".
                    ignored_select =
                        Some(("directory or drive -- listing repopulated", cursor_row));
                    None
                }
                PickerActivation::Ignored => {
                    ignored_select = Some(("non-selectable row", cursor_row));
                    None
                }
            }
        } else {
            None
        }
    };

    if let Some((why, cursor_row)) = ignored_select {
        append_autoload_debug(format_args!(
            "save-picker-overlay: SELECT on row {cursor_row} did not pick a save file: {why}. \
             Move the highlight onto a save file (UP/DOWN) and confirm again."
        ));
    }

    save_picker_stage_picked_file(picked);
}

fn save_picker_file_stage_mouse_click(click: MouseClick) {
    let picked = {
        let mut guard = model::active_save_picker_lock();
        let Some(model) = guard.as_mut() else {
            return;
        };
        if click.button == MouseButton::Right {
            model.go_up();
            return;
        }
        let Some(row) = picker_file_row_hit(model, click) else {
            return;
        };
        model.set_cursor(row);
        match model.row_meaning(row) {
            meaning @ model::PickerRow::DriveCycle => {
                let forward = click.x >= click.w / 2;
                model.cycle_drive(forward);
                append_autoload_debug(format_args!(
                    "save-picker-overlay: left-click row={row} meaning={meaning:?} \
                     outcome=Repopulate direction={}",
                    if forward { "next" } else { "previous" }
                ));
                None
            }
            meaning @ model::PickerRow::NextPage => {
                let forward = click.x >= click.w / 2;
                model.cycle_page(forward);
                append_autoload_debug(format_args!(
                    "save-picker-overlay: left-click row={row} meaning={meaning:?} \
                     outcome=Repopulate direction={}",
                    if forward { "next" } else { "previous" }
                ));
                None
            }
            meaning => {
                let outcome = model.activate_cursor();
                append_autoload_debug(format_args!(
                    "save-picker-overlay: left-click row={row} meaning={meaning:?} \
                     outcome={outcome:?}"
                ));
                match outcome {
                    PickerActivation::PickedFile(path) => Some(path),
                    _ => None,
                }
            }
        }
    };
    save_picker_stage_picked_file(picked);
}

fn save_picker_stage_picked_file(picked: Option<std::path::PathBuf>) {
    let Some(path) = picked else {
        return;
    };
    // Parse the picked save's active character slots (from its own bytes -- no dependency on the
    // game having built its ProfileSummary yet).
    let slots = std::fs::read(&path)
        .ok()
        .map(|bytes| parse_save_character_slots(&bytes))
        .unwrap_or_default();
    if slots.is_empty() {
        // Not a readable save / no characters -- stay in the file browser.
        SAVE_PICKER_OVERLAY_PICK_REJECT_COUNT.fetch_add(1, Ordering::SeqCst);
        set_overlay_status_message(
            crate::model::PickRejection::NoLoadableCharacter.status_message("sl2"),
        );
        append_autoload_debug(format_args!(
            "save-picker-overlay: '{}' has no readable character slots; staying in file browser with visible reason",
            path.display()
        ));
        return;
    }
    append_autoload_debug(format_args!(
        "save-picker-overlay: selected save '{}' -- {} character slots; opening character sub-picker",
        path.display(),
        slots.len()
    ));
    *pending_save_lock() = Some(PendingSave { path, slots });
    SAVE_PICKER_CHAR_CURSOR.store(0, Ordering::SeqCst);
    SAVE_PICKER_STAGE_CHARS.store(1, Ordering::SeqCst);
}

/// Character sub-picker input: up/down move, back returns to the file browser, select commits the
/// chosen slot -- record it for the autoload, activate the redirect, and release the save-check
/// hold.
fn save_picker_character_stage_input(pressed: usize) {
    // Resolve the chosen slot + path under the lock, act (redirect/complete) outside it.
    let act = {
        let guard = pending_save_lock();
        let Some(pending) = guard.as_ref() else {
            // No pending save -> fall back to the file browser.
            SAVE_PICKER_STAGE_CHARS.store(0, Ordering::SeqCst);
            return;
        };
        let n = pending.slots.len().max(1);
        let mut cursor = SAVE_PICKER_CHAR_CURSOR.load(Ordering::SeqCst).min(n - 1);
        if pressed & PICKER_ACT_UP != 0 {
            cursor = (cursor + n - 1) % n;
        }
        if pressed & PICKER_ACT_DOWN != 0 {
            cursor = (cursor + 1) % n;
        }
        SAVE_PICKER_CHAR_CURSOR.store(cursor, Ordering::SeqCst);
        if pressed & PICKER_ACT_BACK != 0 {
            CharacterAct::Back
        } else if pressed & PICKER_ACT_SELECT != 0 {
            CharacterAct::Pick(pending.path.clone(), pending.slots[cursor].slot)
        } else {
            CharacterAct::None
        }
    };
    save_picker_apply_character_act(act);
}

fn save_picker_character_stage_mouse_click(click: MouseClick) {
    let act = {
        if click.button == MouseButton::Right {
            CharacterAct::Back
        } else {
            let guard = pending_save_lock();
            let Some(pending) = guard.as_ref() else {
                SAVE_PICKER_STAGE_CHARS.store(0, Ordering::SeqCst);
                return;
            };
            let has_status = save_picker_overlay_status_message().is_some();
            let Some(row) = picker_character_row_hit(click, pending.slots.len(), has_status) else {
                return;
            };
            SAVE_PICKER_CHAR_CURSOR.store(row, Ordering::SeqCst);
            CharacterAct::Pick(pending.path.clone(), pending.slots[row].slot)
        }
    };
    save_picker_apply_character_act(act);
}

fn save_picker_apply_character_act(act: CharacterAct) {
    match act {
        CharacterAct::None => {}
        CharacterAct::Back => {
            *pending_save_lock() = None;
            SAVE_PICKER_STAGE_CHARS.store(0, Ordering::SeqCst);
        }
        CharacterAct::Pick(path, slot) => {
            // Defer the actual redirect activation + MinHook install to the game-task thread (via
            // this request): it runs the risky install off the render thread, and the game task is
            // alive at pick time (the boot is still held -- loading only starts once the pick
            // releases the hold). Record the chosen slot now so the character list stays selected.
            MISSING_SAVE_PICKER_SELECTED_SLOT.store(slot, Ordering::SeqCst);
            *save_picker_complete_request_lock() = Some((path, slot));
            append_autoload_debug(format_args!(
                "save-picker-overlay: character slot {slot} chosen; completion requested (game-task thread)"
            ));
        }
    }
}

enum CharacterAct {
    None,
    Back,
    Pick(std::path::PathBuf, usize),
}

// ---- Rendering ----

// Overlay palette (reuses the boot bar's understated language; dark panel, off-white highlight).
const PICKER_RGB_PANEL: [u8; 3] = [12, 12, 14];
const PICKER_RGB_TITLE: [u8; 3] = [214, 208, 190];
const PICKER_RGB_DIM: [u8; 3] = [120, 117, 108];
const PICKER_RGB_SEL_BAR: [u8; 3] = [58, 54, 44];
const PICKER_RGB_SEL_TEXT: [u8; 3] = [238, 232, 214];
const PICKER_RGB_RULE: [u8; 3] = [40, 38, 33];
const PICKER_RGB_WARNING: [u8; 3] = [220, 160, 82];
/// A filesystem location: the header's current directory and the drive row's path field. Cool
/// against the warm parchment of the saves, so "where you are" and "what you can load" are
/// separable at a glance rather than one undifferentiated column of the same grey.
const PICKER_RGB_PATH: [u8; 3] = [126, 166, 198];
/// A directory row -- somewhere to go, not something to load.
const PICKER_RGB_DIR: [u8; 3] = [142, 170, 196];
/// A save file row: the only rows that load anything, so they carry the warmest colour on screen.
const PICKER_RGB_SAVE: [u8; 3] = [218, 198, 148];
/// The explanatory line under a warning headline. Brighter than `DIM`, which was too dark for a
/// sentence the user is expected to act on.
const PICKER_RGB_DETAIL: [u8; 3] = [198, 194, 184];
/// Text the user is typing right now.
const PICKER_RGB_EDIT: [u8; 3] = [240, 236, 220];
/// The part of an autocomplete suggestion the user has not typed yet.
const PICKER_RGB_SUGGEST: [u8; 3] = [96, 112, 124];

/// Truncate `text` so it fits within `max_px` at the boot font's scale (drops the tail; keeps a
/// trailing marker when clipped).
fn picker_fit_text(text: &str, max_px: usize, scale: usize) -> String {
    let max_chars = picker_columns(max_px, scale);
    if text.chars().count() <= max_chars {
        return text.to_owned();
    }
    let keep = max_chars.saturating_sub(1);
    let mut out: String = text.chars().take(keep).collect();
    out.push('>');
    out
}

/// Characters that fit in `max_px` at `scale`.
fn picker_columns(max_px: usize, scale: usize) -> usize {
    (max_px / (BOOT_VIEW_GLYPH_ADV * scale)).max(1)
}

/// Break `text` into at most `max_lines` lines of `max_px` width, on word boundaries where it can
/// and mid-word where a single word is longer than the line. The last line is ellipsised when
/// there is more text than lines.
///
/// The status banner used to be one `HEADLINE: detail` line truncated to the panel width, so
/// every sentence longer than the panel lost its tail -- including the part naming the save.
fn picker_wrap_text(text: &str, max_px: usize, scale: usize, max_lines: usize) -> Vec<String> {
    let cols = picker_columns(max_px, scale);
    let mut lines: Vec<String> = Vec::new();
    let mut current = String::new();
    for word in text.split_whitespace() {
        let candidate = if current.is_empty() {
            word.to_owned()
        } else {
            format!("{current} {word}")
        };
        if candidate.chars().count() <= cols {
            current = candidate;
            continue;
        }
        if !current.is_empty() {
            lines.push(std::mem::take(&mut current));
            if lines.len() == max_lines {
                break;
            }
        }
        // A single word wider than the line: hard-split it rather than overflow the panel.
        let mut rest: String = word.to_owned();
        while rest.chars().count() > cols && lines.len() < max_lines {
            lines.push(rest.chars().take(cols).collect());
            rest = rest.chars().skip(cols).collect();
        }
        if lines.len() == max_lines {
            break;
        }
        current = rest;
    }
    if lines.len() < max_lines && !current.is_empty() {
        lines.push(current);
    }
    // Mark a clipped tail on the last line the same way `picker_fit_text` marks a clipped line.
    let consumed: usize = lines.iter().map(|line| line.chars().count()).sum();
    let total = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if consumed < total.chars().count().saturating_sub(lines.len())
        && let Some(last) = lines.last_mut()
    {
        let keep = cols.saturating_sub(1);
        if last.chars().count() > keep {
            *last = last.chars().take(keep).collect();
        }
        last.push('>');
    }
    lines
}

/// Draw the file browser onto an existing full-frame buffer (`w*h` RGBA8) that already holds the
/// boot loading bar at the bottom. The picker occupies a bounded panel in the upper region so the
/// game's own loading-bar language (bottom strip) stays visible underneath -- the picker is
/// composited with the bar, not in place of it. Reads the live model; render-thread safe (pure
/// read + CPU raster). Returns false if there is no model.
pub fn overlay_save_picker_onto(buf: &mut [u8], w: usize, h: usize) -> bool {
    let Some(m) = picker_overlay_metrics(w, h) else {
        return false;
    };
    boot_fill_rect(
        buf,
        w,
        h,
        m.margin_x,
        m.panel_top,
        m.content_w,
        m.panel_bottom.saturating_sub(m.panel_top),
        PICKER_RGB_PANEL,
    );

    // Stage two: choose which character (save slot) in the picked file to load.
    if SAVE_PICKER_STAGE_CHARS.load(Ordering::SeqCst) != 0 {
        return overlay_character_stage_onto(buf, w, h, m);
    }

    let mut guard = model::active_save_picker_lock();
    let Some(model) = guard.as_mut() else {
        return false;
    };
    let has_status = model.status_message().is_some();
    let info_lines = picker_file_info_lines(model);
    // The view tells the model how many rows it can draw, every frame. The model's cursor and
    // scroll window then address exactly what is on screen, whatever the frame height is.
    model.set_row_capacity(m.rows_visible(info_lines, has_status));

    let mut y = m.panel_top + m.pad;
    boot_draw_text_rgb(
        buf,
        w,
        h,
        m.text_x,
        y,
        "SELECT SAVE FILE",
        PICKER_RGB_TITLE,
        m.title_scale,
    );
    y += m.title_h + m.gap;
    picker_rule(buf, w, h, m, y);
    y += m.rule_h + m.gap;

    // Only when row 0 is not already showing it.
    if model.drive_row().is_none() {
        let loc_line = picker_fit_text(&model.location_label(), m.text_w, m.scale);
        boot_draw_text_rgb(buf, w, h, m.text_x, y, &loc_line, PICKER_RGB_PATH, m.scale);
        y += m.line_h;
    }
    let mode_line = format!(
        "SHOWING *.{}   {} ITEMS   PAGE {}/{}",
        model.extension().to_ascii_uppercase(),
        model.entry_count(),
        model.page() + 1,
        model.page_count()
    );
    boot_draw_text_rgb(
        buf,
        w,
        h,
        m.text_x,
        y,
        &picker_fit_text(&mode_line, m.text_w, m.scale),
        PICKER_RGB_DIM,
        m.scale,
    );
    y += m.line_h + m.gap;
    if let Some(message) = model.status_message() {
        picker_draw_status(buf, w, h, m, y, message);
    }

    // Rows start where the hit test says they do, not where the header happened to end.
    let mut y = m.rows_start_y(info_lines, has_status);
    picker_rule(buf, w, h, m, y.saturating_sub(m.gap + m.rule_h));
    let rows_bottom = m.rows_bottom_y();
    let cursor = model.cursor();
    for row in 0..model.row_capacity() {
        if y + m.line_h >= rows_bottom {
            break;
        }
        if model.drive_row() == Some(row) {
            picker_draw_drive_row(buf, w, h, m, y, model, row, row == cursor);
            y += m.row_step;
            continue;
        }
        let label = model.row_label_ascii(row);
        if label.is_empty() {
            continue;
        }
        let color = picker_row_rgb(&model.row_meaning(row));
        picker_draw_row(buf, w, h, m, y, &label, row == cursor, color);
        y += m.row_step;
    }

    picker_draw_footer(
        buf,
        w,
        h,
        m,
        "CLICK A ROW TO OPEN   RIGHT CLICK GOES UP   LEFT/RIGHT CHANGES DRIVE",
    );
    true
}

/// The path the user is typing into the drive row's field, when they are typing one.
///
/// # Why the overlay needed its own text entry
///
/// The native `05_010` surface puts a complete-path editor to the right of the drive cells, with
/// autocomplete, and the user navigates by typing a path as often as by clicking folders. The
/// DLL-drawn overlay had a drive row that was a label -- `DRIVES [C:] >S:< [Z:]` -- with no field,
/// nothing editable and no completion, so the two pickers behaved differently on their most-used
/// row. `None` means nobody is typing; `Some` is the buffer, and its presence is what routes keys
/// here instead of to the cursor.
static PATH_EDIT: Mutex<Option<String>> = Mutex::new(None);

fn path_edit_lock() -> std::sync::MutexGuard<'static, Option<String>> {
    PATH_EDIT
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// True while the drive row's path field is taking keystrokes.
pub fn save_picker_path_edit_active() -> bool {
    path_edit_lock().is_some()
}

/// The text typed so far, if the field is being edited.
fn path_edit_text() -> Option<String> {
    path_edit_lock().clone()
}

/// Start editing, seeded with the folder the browser is standing in.
fn path_edit_begin(seed: String) {
    *path_edit_lock() = Some(seed);
}

fn path_edit_cancel() {
    *path_edit_lock() = None;
}

// Virtual-key codes for text entry.
const VK_SHIFT: i32 = 0x10;
const VK_ESCAPE: i32 = 0x1b;
const VK_TAB: i32 = 0x09;
const VK_SPACE: i32 = 0x20;
const VK_OEM_1: i32 = 0xba;
const VK_OEM_MINUS: i32 = 0xbd;
const VK_OEM_PERIOD: i32 = 0xbe;
const VK_OEM_2: i32 = 0xbf;
const VK_OEM_5: i32 = 0xdc;

/// The character a key produces in the path field, or `None` for keys that are not text.
///
/// Deliberately small: a path is letters, digits, separators and the handful of punctuation that
/// appears in real folder names. Anything else is better ignored than guessed at, because this
/// maps virtual keys rather than asking the OS for the layout's character.
fn path_edit_char_for_vk(vk: i32, shift: bool) -> Option<char> {
    match vk {
        0x41..=0x5a => {
            let base = (b'A' + (vk - 0x41) as u8) as char;
            Some(if shift {
                base
            } else {
                base.to_ascii_lowercase()
            })
        }
        0x30..=0x39 => {
            let digit = (b'0' + (vk - 0x30) as u8) as char;
            Some(if shift { digit } else { digit })
        }
        VK_SPACE => Some(' '),
        VK_OEM_5 => Some('\\'),
        VK_OEM_2 => Some('/'),
        VK_OEM_1 => Some(if shift { ':' } else { ';' }),
        VK_OEM_PERIOD => Some('.'),
        VK_OEM_MINUS => Some(if shift { '_' } else { '-' }),
        _ => None,
    }
}

/// Feed one key press to the path field. Returns whether the key was consumed, so the caller
/// knows not to also treat it as cursor navigation.
fn save_picker_path_edit_key(vk: i32, shift: bool) -> bool {
    let Some(mut text) = path_edit_text() else {
        return false;
    };
    match vk {
        VK_RETURN => {
            let committed = {
                let mut guard = model::active_save_picker_lock();
                match guard.as_mut() {
                    Some(model) => match model.set_current_dir_from_text(&text) {
                        Ok(_) => {
                            model.clear_status_message();
                            true
                        }
                        Err(err) => {
                            model.set_rejected_path_text(&text);
                            model.set_status_message(err.status_message());
                            false
                        }
                    },
                    None => false,
                }
            };
            if committed {
                path_edit_cancel();
            }
            append_autoload_debug(format_args!(
                "save-picker-overlay: path field committed='{text}' accepted={committed}"
            ));
        }
        VK_ESCAPE => path_edit_cancel(),
        VK_BACK => {
            text.pop();
            *path_edit_lock() = Some(text);
        }
        VK_TAB => {
            if let Some(suggestion) = crate::autocomplete::suggestion_for(&text) {
                *path_edit_lock() = Some(suggestion);
            }
        }
        other => {
            let Some(ch) = path_edit_char_for_vk(other, shift) else {
                return false;
            };
            text.push(ch);
            *path_edit_lock() = Some(text);
        }
    }
    true
}

/// A full-width hairline across the panel's content area.
fn picker_rule(buf: &mut [u8], w: usize, h: usize, m: PickerOverlayMetrics, y: usize) {
    boot_fill_rect(
        buf,
        w,
        h,
        m.text_x,
        y,
        m.text_w,
        m.scale.max(1),
        PICKER_RGB_RULE,
    );
}

/// The status banner: headline on its own line in the warning colour, detail wrapped below it.
///
/// Always occupies [`PICKER_STATUS_LINES`] whatever it draws, because the rows below are placed
/// from that same constant.
fn picker_draw_status(
    buf: &mut [u8],
    w: usize,
    h: usize,
    m: PickerOverlayMetrics,
    top: usize,
    message: &PickerStatusMessage,
) {
    let mut y = top;
    boot_draw_text_rgb(
        buf,
        w,
        h,
        m.text_x,
        y,
        &picker_fit_text(message.headline(), m.text_w, m.scale),
        PICKER_RGB_WARNING,
        m.scale,
    );
    y += m.line_h;
    for line in picker_wrap_text(
        message.detail(),
        m.text_w,
        m.scale,
        PICKER_STATUS_DETAIL_LINES,
    ) {
        boot_draw_text_rgb(buf, w, h, m.text_x, y, &line, PICKER_RGB_DETAIL, m.scale);
        y += m.line_h;
    }
}

/// The colour a row is drawn in, by what activating it would do.
///
/// One flat grey for every row meant the only way to tell a folder from a save was to read the
/// trailing slash. Saves carry the warmest colour because they are the only rows that load
/// anything; navigation rows are dim because they are scaffolding.
fn picker_row_rgb(meaning: &model::PickerRow) -> [u8; 3] {
    match meaning {
        model::PickerRow::Dir(_) => PICKER_RGB_DIR,
        model::PickerRow::File(_) | model::PickerRow::NewFile(_) => PICKER_RGB_SAVE,
        _ => PICKER_RGB_DIM,
    }
}

/// Draw the selection bar for a row.
fn picker_draw_selection_bar(
    buf: &mut [u8],
    w: usize,
    h: usize,
    m: PickerOverlayMetrics,
    y: usize,
) {
    boot_fill_rect(
        buf,
        w,
        h,
        m.margin_x + m.pad / 2,
        y.saturating_sub(m.scale * 2),
        m.content_w.saturating_sub(m.pad),
        m.line_h + m.scale * 4,
        PICKER_RGB_SEL_BAR,
    );
}

/// Row 0: the drive cells and the complete-path field, which is the native surface's layout.
///
/// The cells are the same `[<] [C:] >S:< [Z:] [>]` the native strip stages, and the field to
/// their right is the same complete-path editor -- reached by walking right off the last drive
/// (`cycle_drive_from_drive_strip`), typed into directly, completed with Tab, committed with
/// Enter.
fn picker_draw_drive_row(
    buf: &mut [u8],
    w: usize,
    h: usize,
    m: PickerOverlayMetrics,
    y: usize,
    model: &SavePickerModel,
    row: usize,
    row_selected: bool,
) {
    if row_selected {
        picker_draw_selection_bar(buf, w, h, m, y);
    }
    let adv = BOOT_VIEW_GLYPH_ADV * m.scale;
    let focus = model.drive_strip_focus();
    let mut x = m.text_x + adv * 2;
    for cell in 0..model.drive_strip_cell_count() {
        let Some(label) = model.drive_row_cell_label(row, cell) else {
            continue;
        };
        let focused = row_selected && focus == Some(model::DriveStripFocus::Cell(cell));
        let color = if focused {
            PICKER_RGB_SEL_TEXT
        } else {
            PICKER_RGB_PATH
        };
        boot_draw_text_rgb(buf, w, h, x, y, &label, color, m.scale);
        x += (label.chars().count() + 1) * adv;
    }

    // The complete-path field fills the rest of the row.
    let field_x = x + adv;
    let field_w = (m.text_x + m.text_w).saturating_sub(field_x);
    let path_focused = row_selected && focus == Some(model::DriveStripFocus::CurrentPath);
    match path_edit_text() {
        Some(typed) => {
            let caret = format!("{typed}_");
            boot_draw_text_rgb(
                buf,
                w,
                h,
                field_x,
                y,
                &picker_fit_text(&caret, field_w, m.scale),
                PICKER_RGB_EDIT,
                m.scale,
            );
            // The completion the Tab key would accept, drawn dim after the caret.
            if let Some(suggestion) = crate::autocomplete::suggestion_for(&typed)
                && let Some(rest) = suggestion.strip_prefix(typed.as_str())
            {
                let rest_x = field_x + caret.chars().count() * adv;
                let rest_w = (field_x + field_w).saturating_sub(rest_x);
                boot_draw_text_rgb(
                    buf,
                    w,
                    h,
                    rest_x,
                    y,
                    &picker_fit_text(rest, rest_w, m.scale),
                    PICKER_RGB_SUGGEST,
                    m.scale,
                );
            }
        }
        None => {
            let shown = model
                .rejected_path_text()
                .map(str::to_owned)
                .unwrap_or_else(|| model.current_dir().display().to_string());
            let color = if path_focused {
                PICKER_RGB_SEL_TEXT
            } else {
                PICKER_RGB_PATH
            };
            boot_draw_text_rgb(
                buf,
                w,
                h,
                field_x,
                y,
                &picker_fit_text(&shown, field_w, m.scale),
                color,
                m.scale,
            );
        }
    }
}

/// One list row, with the selection bar behind it when it is the cursor.
fn picker_draw_row(
    buf: &mut [u8],
    w: usize,
    h: usize,
    m: PickerOverlayMetrics,
    y: usize,
    label: &str,
    selected: bool,
    color: [u8; 3],
) {
    if selected {
        picker_draw_selection_bar(buf, w, h, m, y);
    }
    let (color, prefix) = if selected {
        (PICKER_RGB_SEL_TEXT, "> ")
    } else {
        (color, "  ")
    };
    let text = picker_fit_text(&format!("{prefix}{label}"), m.text_w, m.scale);
    boot_draw_text_rgb(buf, w, h, m.text_x, y, &text, color, m.scale);
}

/// The hint line at the bottom of the panel.
fn picker_draw_footer(buf: &mut [u8], w: usize, h: usize, m: PickerOverlayMetrics, hint: &str) {
    let footer_y = m.panel_bottom.saturating_sub(m.line_h + m.pad);
    picker_rule(buf, w, h, m, footer_y.saturating_sub(m.pad));
    boot_draw_text_rgb(
        buf,
        w,
        h,
        m.text_x,
        footer_y,
        &picker_fit_text(hint, m.text_w, m.scale),
        PICKER_RGB_DIM,
        m.scale,
    );
}

/// Draw the character sub-picker (stage two): the picked save's active characters, one per row.
fn overlay_character_stage_onto(
    buf: &mut [u8],
    w: usize,
    h: usize,
    m: PickerOverlayMetrics,
) -> bool {
    let guard = pending_save_lock();
    let Some(pending) = guard.as_ref() else {
        return false;
    };
    let status = save_picker_overlay_status_message();

    let mut y = m.panel_top + m.pad;
    boot_draw_text_rgb(
        buf,
        w,
        h,
        m.text_x,
        y,
        "SELECT CHARACTER",
        PICKER_RGB_TITLE,
        m.title_scale,
    );
    y += m.title_h + m.gap;
    picker_rule(buf, w, h, m, y);
    y += m.rule_h + m.gap;

    let name = pending
        .path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("?")
        .to_owned();
    boot_draw_text_rgb(
        buf,
        w,
        h,
        m.text_x,
        y,
        &picker_fit_text(&name, m.text_w, m.scale),
        PICKER_RGB_DIM,
        m.scale,
    );
    y += m.line_h + m.gap;
    if let Some(message) = status.as_ref() {
        picker_draw_status(buf, w, h, m, y, message);
    }

    let mut y = m.rows_start_y(PICKER_CHARACTER_INFO_LINES, status.is_some());
    picker_rule(buf, w, h, m, y.saturating_sub(m.gap + m.rule_h));
    let rows_bottom = m.rows_bottom_y();
    let cursor = SAVE_PICKER_CHAR_CURSOR
        .load(Ordering::SeqCst)
        .min(pending.slots.len().saturating_sub(1));
    for (i, info) in pending.slots.iter().enumerate() {
        if y + m.line_h >= rows_bottom {
            break;
        }
        let label = format!("SLOT {}   {}   LV {}", info.slot, info.name, info.level);
        picker_draw_row(buf, w, h, m, y, &label, i == cursor, PICKER_RGB_SAVE);
        y += m.row_step;
    }

    picker_draw_footer(
        buf,
        w,
        h,
        m,
        "CLICK A CHARACTER TO LOAD   RIGHT CLICK GOES BACK TO FILES",
    );
    true
}

#[cfg(test)]
mod mouse_hit_tests {
    use super::*;

    /// This module's namespace inside the crate-wide [`crate::picker_scratch_dir`], which is
    /// what keys the directory to this process as well as to `tag`. The prefix is all that is
    /// left here: one implementation of the wipe-and-create, so the pid cannot be present in one
    /// module and missing in the next.
    fn scratch_dir(tag: &str) -> std::path::PathBuf {
        crate::picker_scratch_dir(&format!("mouse-hit-{tag}"))
    }

    fn click_on_file_row(model: &SavePickerModel, row: usize) -> MouseClick {
        let w = 1920;
        let h = 1080;
        let metrics = picker_overlay_metrics(w, h).expect("metrics");
        let y = picker_file_rows_start_y(
            metrics,
            picker_file_info_lines(model),
            model.status_message().is_some(),
        ) + row * metrics.row_step
            + metrics.line_h / 2;
        MouseClick {
            x: metrics.margin_x + metrics.content_w / 2,
            y,
            w,
            h,
            button: MouseButton::Left,
        }
    }

    /// The overlay is not the native window and must not be held to its ten slots.
    ///
    /// `PICKER_ROW_COUNT` is a fact about `05_010_ProfileSelect`, which has ten profile records
    /// and cannot have eleven. This surface rasterises its own rows, so the only limit is the
    /// panel -- and while it shared the native constant a user browsing a folder of thirty saves
    /// paged through three screens of a list that fits on one.
    #[test]
    fn the_overlay_shows_far_more_rows_than_the_native_window_can() {
        let metrics = picker_overlay_metrics(1920, 1080).expect("metrics");
        let rows = metrics.rows_visible(PICKER_FILE_INFO_LINES, false);
        assert!(
            rows > model::PICKER_ROW_COUNT * 2,
            "1080p should fit well over twice the native ten rows, fits {rows}"
        );
        assert!(
            rows <= PICKER_OVERLAY_ROW_MAX,
            "the cap must hold, got {rows}"
        );
        // A banner costs rows, and must never cost so many that the list disappears.
        let with_status = metrics.rows_visible(PICKER_FILE_INFO_LINES, true);
        assert!(
            with_status >= model::PICKER_ROW_COUNT,
            "a banner left only {with_status} rows"
        );
    }

    /// The picker's body text is the boot bar's body text, and the heading is one step above it.
    ///
    /// Judged on screen: with the picker on its own rule the rows were noticeably larger than the
    /// bar label beneath them, which reads as two unrelated overlays rather than one surface.
    #[test]
    fn body_text_matches_the_loading_bar_and_the_title_outranks_it() {
        for (w, h) in [(1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)] {
            let metrics = picker_overlay_metrics(w, h).expect("metrics");
            assert_eq!(
                metrics.scale,
                er_loading_bar_core::boot_text_scale(h),
                "{w}x{h} must draw body text at the boot bar's own size"
            );
            assert!(metrics.title_scale > metrics.scale);
            assert!(
                metrics.pad >= metrics.line_h,
                "the panel needs real padding, got {}",
                metrics.pad
            );
        }
    }

    #[test]
    fn mouse_hit_testing_maps_file_browser_rows_to_the_same_model_rows_drawn() {
        let dir = scratch_dir("file-rows");
        std::fs::create_dir_all(dir.join("subdir")).expect("subdir must be creatable");
        let model = SavePickerModel::open(&dir, "sl2");
        let parent = model.parent_row().expect("temp dir has a parent row");
        assert_eq!(
            picker_file_row_hit(&model, click_on_file_row(&model, parent)),
            Some(parent)
        );
        let dir_row = parent + 1;
        assert!(matches!(
            model.row_meaning(dir_row),
            model::PickerRow::Dir(_)
        ));
        assert_eq!(
            picker_file_row_hit(&model, click_on_file_row(&model, dir_row)),
            Some(dir_row)
        );
        let metrics = picker_overlay_metrics(1920, 1080).expect("metrics");
        assert_eq!(
            picker_file_row_hit(
                &model,
                MouseClick {
                    x: 0,
                    y: metrics.panel_top,
                    w: 1920,
                    h: 1080,
                    button: MouseButton::Left,
                }
            ),
            None,
            "clicks outside the row band must not activate a stale cursor"
        );
    }

    #[test]
    fn mouse_hit_testing_maps_character_rows_to_slot_indices() {
        let w = 1920;
        let h = 1080;
        let metrics = picker_overlay_metrics(w, h).expect("metrics");
        let first = picker_character_rows_start_y(metrics, false);
        for row in 0..3 {
            let click = MouseClick {
                x: metrics.margin_x + metrics.content_w / 2,
                y: first + row * metrics.row_step + metrics.line_h / 2,
                w,
                h,
                button: MouseButton::Left,
            };
            assert_eq!(picker_character_row_hit(click, 3, false), Some(row));
        }
        let below_slots = MouseClick {
            x: metrics.margin_x + metrics.content_w / 2,
            y: first + 3 * metrics.row_step + metrics.line_h / 2,
            w,
            h,
            button: MouseButton::Left,
        };
        assert_eq!(picker_character_row_hit(below_slots, 3, false), None);
    }
}
