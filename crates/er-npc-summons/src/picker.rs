//! The duel picker: which NPC to fight, chosen while the sign is being placed.
//!
//! Drawn through the process's one imgui context (see [`crate::overlay`]), centred and scaled to
//! the display. Three ways to choose, all live at once:
//!
//! * the keyboard: arrows and Enter, Backspace to cancel;
//! * a pad: d-pad and A, B to cancel;
//! * the mouse: click a name, or Cancel. In the world the game owns the pointer for the camera,
//!   so the cursor is only free while one of the game's own menus is open.
//!
//! Escape does not cancel. It is the key that opens the game's menu, which is how a mouse player
//! gets a cursor in the first place, so cancelling on it closed the picker in the same press that
//! made it clickable (reported 2026-10-06).
//!
//! While it is open the game does not get its keys, buttons or mouse:
//!
//! * the arrows, Enter, keypad Enter and Backspace are blanked in the game's DirectInput keyboard
//!   reads (`er-dinput-suppress-core`, chained on the shared `GetDeviceState` union); the picker
//!   itself reads `GetAsyncKeyState`, which that does not touch;
//! * d-pad up/down, A and B are cleared in the game's `XInputGetState` reads ([`crate::pad`]),
//!   and the picker reads the raw buttons that hook keeps;
//! * the game is told a menu has the mouse ([`crate::cursor`]), so it frees and shows the cursor
//!   and stops turning the camera, and the left button is blanked while the pointer is over the
//!   picker, so a click does not also land in the world.
//!
//! A key or button held when the picker closes stays taken until it is released.

#![cfg(windows)]

use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use hudhook::imgui::{Condition, Ui, WindowHoveredFlags};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, VK_BACK, VK_DOWN, VK_RETURN, VK_UP,
};

/// The DirectInput scan codes of the keys the picker is driven by: up, down, Enter, keypad Enter,
/// Backspace.
const DIK_UP: u8 = 0xc8;
const DIK_DOWN: u8 = 0xd0;
const DIK_RETURN: u8 = 0x1c;
const DIK_NUMPADENTER: u8 = 0x9c;
const DIK_BACK: u8 = 0x0e;
const PICKER_KEYS: [u8; 5] = [DIK_UP, DIK_DOWN, DIK_RETURN, DIK_NUMPADENTER, DIK_BACK];
use windows::Win32::UI::Input::XboxController::{
    XINPUT_GAMEPAD_A, XINPUT_GAMEPAD_B, XINPUT_GAMEPAD_BUTTON_FLAGS, XINPUT_GAMEPAD_DPAD_DOWN,
    XINPUT_GAMEPAD_DPAD_UP, XINPUT_STATE, XInputGetState,
};

use crate::log::summons_log;

/// What the player did with the list this frame.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Choice {
    Picked(usize),
    Cancelled,
}

struct View {
    rows: Vec<String>,
    cursor: usize,
}

static VIEW: Mutex<Option<View>> = Mutex::new(None);
/// Lock-free "is it open" for the draw fast path.
static OPEN: AtomicBool = AtomicBool::new(false);

/// No click is waiting.
const NO_CLICK: usize = usize::MAX;
/// A row the mouse picked on the render thread, for the game task to act on.
static CLICKED_ROW: AtomicUsize = AtomicUsize::new(NO_CLICK);
/// The Cancel button was clicked on the render thread.
static CLICKED_CANCEL: AtomicBool = AtomicBool::new(false);

/// The layout is authored for a 1080-line display and scaled from there.
const REFERENCE_DISPLAY_HEIGHT: f32 = 1080.0;
/// Window width at the reference height.
const WINDOW_WIDTH: f32 = 460.0;

/// Which inputs were down last frame, for edge detection.
#[derive(Default)]
pub(crate) struct Edges {
    up: bool,
    down: bool,
    confirm: bool,
    back: bool,
    /// Swallow the first frame: the button that used the item may still be held.
    armed: bool,
}

pub(crate) fn open(rows: Vec<String>) {
    CLICKED_ROW.store(NO_CLICK, Ordering::Release);
    CLICKED_CANCEL.store(false, Ordering::Release);
    if let Ok(mut view) = VIEW.lock() {
        *view = Some(View { rows, cursor: 0 });
        OPEN.store(true, Ordering::Release);
    }
    er_dinput_suppress_core::set_keys_taken(&PICKER_KEYS);
    crate::pad::set_taking(true);
}

/// Whether the picker is on screen.
pub(crate) fn is_open() -> bool {
    OPEN.load(Ordering::Acquire)
}

pub(crate) fn close() {
    OPEN.store(false, Ordering::Release);
    er_dinput_suppress_core::set_keys_taken(&[]);
    crate::pad::set_taking(false);
    er_dinput_suppress_core::set_pointer_over_overlay(false);
    summons_log(format_args!(
        "picker: closed; keyboard reads hooked {}, picker keys blanked {} time(s), pad reads \
         cleared {} time(s), menu-mouse answered {} time(s)",
        er_dinput_suppress_core::keyboard_hook_fires(),
        er_dinput_suppress_core::suppressed_keys(),
        crate::pad::suppressed(),
        crate::cursor::forced()
    ));
    if let Ok(mut view) = VIEW.lock() {
        *view = None;
    }
}

fn key_down(vk: u16) -> bool {
    // SAFETY: no preconditions; the high bit is "down now".
    (unsafe { GetAsyncKeyState(i32::from(vk)) } as u16 & 0x8000) != 0
}

fn pad_buttons() -> XINPUT_GAMEPAD_BUTTON_FLAGS {
    // The hook's copy, from before it cleared the picker's buttons; a direct read would go
    // through that hook and see them cleared.
    if let Some(raw) = crate::pad::raw_buttons() {
        return XINPUT_GAMEPAD_BUTTON_FLAGS(raw);
    }
    let mut state = XINPUT_STATE::default();
    // SAFETY: writes one `XINPUT_STATE`; a disconnected pad returns an error and is ignored.
    if unsafe { XInputGetState(0, &mut state) } == 0 {
        state.Gamepad.wButtons
    } else {
        XINPUT_GAMEPAD_BUTTON_FLAGS(0)
    }
}

/// Arm the click blanking once `dinput8.dll` is loaded; retried each frame until it is.
fn arm_click_suppression() {
    static ARMED: AtomicBool = AtomicBool::new(false);
    static SAID: AtomicBool = AtomicBool::new(false);
    if ARMED.load(Ordering::Relaxed) {
        return;
    }
    // SAFETY: called from the game task, after frames have run.
    match unsafe { er_dinput_suppress_core::install_mouse_suppression() } {
        Ok(addr) => {
            ARMED.store(true, Ordering::Relaxed);
            summons_log(format_args!(
                "picker: mouse click suppression armed at {addr:#x}"
            ));
        }
        Err(status) => {
            if !SAID.swap(true, Ordering::Relaxed) {
                summons_log(format_args!(
                    "picker: mouse click suppression not armed yet: {status:?} (retrying)"
                ));
            }
        }
    }
}

/// Arm the keyboard blanking once `dinput8.dll` is loaded; retried each frame until it is.
fn arm_key_suppression() {
    static ARMED: AtomicBool = AtomicBool::new(false);
    static SAID: AtomicBool = AtomicBool::new(false);
    if ARMED.load(Ordering::Relaxed) {
        return;
    }
    // SAFETY: called from the game task, after frames have run.
    match unsafe { er_dinput_suppress_core::install_keyboard_suppression() } {
        Ok(addr) => {
            ARMED.store(true, Ordering::Relaxed);
            summons_log(format_args!(
                "picker: keyboard suppression armed at {addr:#x}"
            ));
        }
        Err(status) => {
            if !SAID.swap(true, Ordering::Relaxed) {
                summons_log(format_args!(
                    "picker: keyboard suppression not armed yet: {status:?} (retrying)"
                ));
            }
        }
    }
}

/// Poll once per frame from the game task.
pub(crate) fn poll(edges: &mut Edges) -> Option<Choice> {
    arm_click_suppression();
    arm_key_suppression();
    crate::pad::try_install();
    if !OPEN.load(Ordering::Acquire) {
        *edges = Edges::default();
        return None;
    }
    let clicked = CLICKED_ROW.swap(NO_CLICK, Ordering::AcqRel);
    if clicked != NO_CLICK {
        return Some(Choice::Picked(clicked));
    }
    if CLICKED_CANCEL.swap(false, Ordering::AcqRel) {
        return Some(Choice::Cancelled);
    }
    let pad = pad_buttons();
    let up = key_down(VK_UP.0) || pad.contains(XINPUT_GAMEPAD_DPAD_UP);
    let down = key_down(VK_DOWN.0) || pad.contains(XINPUT_GAMEPAD_DPAD_DOWN);
    let confirm = key_down(VK_RETURN.0) || pad.contains(XINPUT_GAMEPAD_A);
    let back = key_down(VK_BACK.0) || pad.contains(XINPUT_GAMEPAD_B);
    let pressed = |now: bool, before: bool| now && !before;
    let was = core::mem::replace(
        edges,
        Edges {
            up,
            down,
            confirm,
            back,
            armed: true,
        },
    );
    if !was.armed {
        return None;
    }
    let mut view = VIEW.lock().ok()?;
    let view = view.as_mut()?;
    if view.rows.is_empty() {
        return Some(Choice::Cancelled);
    }
    if pressed(up, was.up) {
        view.cursor = view.cursor.checked_sub(1).unwrap_or(view.rows.len() - 1);
    }
    if pressed(down, was.down) {
        view.cursor = (view.cursor + 1) % view.rows.len();
    }
    if pressed(confirm, was.confirm) {
        return Some(Choice::Picked(view.cursor));
    }
    if pressed(back, was.back) {
        return Some(Choice::Cancelled);
    }
    None
}

/// Draw the list onto a live imgui frame; a no-op while it is closed.
pub(crate) fn draw(ui: &Ui) {
    if !OPEN.load(Ordering::Acquire) {
        er_dinput_suppress_core::set_pointer_over_overlay(false);
        return;
    }
    let Ok(mut view) = VIEW.lock() else {
        return;
    };
    let Some(view) = view.as_mut() else {
        return;
    };
    let [display_w, display_h] = ui.io().display_size;
    let scale = (display_h / REFERENCE_DISPLAY_HEIGHT).max(1.0);
    let mut hovered = false;
    ui.window("Duel: choose an opponent###er-npc-summons-picker")
        .position([display_w * 0.5, display_h * 0.4], Condition::Always)
        .position_pivot([0.5, 0.5])
        .size([WINDOW_WIDTH * scale, 0.0], Condition::Always)
        .collapsible(false)
        .resizable(false)
        .movable(false)
        .build(|| {
            ui.set_window_font_scale(scale);
            for (index, name) in view.rows.iter().enumerate() {
                if ui
                    .selectable_config(name)
                    .selected(index == view.cursor)
                    .build()
                {
                    view.cursor = index;
                    CLICKED_ROW.store(index, Ordering::Release);
                }
                if ui.is_item_hovered() {
                    view.cursor = index;
                }
            }
            ui.separator();
            if ui.button("Cancel") {
                CLICKED_CANCEL.store(true, Ordering::Release);
            }
            ui.text_disabled("Click a name, or Up/Down and Enter, or d-pad and A.");
            ui.text_disabled("Backspace or B cancels.");
            hovered = ui.is_window_hovered_with_flags(WindowHoveredFlags::ROOT_AND_CHILD_WINDOWS);
        });
    er_dinput_suppress_core::set_pointer_over_overlay(hovered || ui.io().want_capture_mouse);
}
