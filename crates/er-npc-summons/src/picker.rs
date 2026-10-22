//! The duel picker: which NPC to fight, chosen while the sign is being placed.
//!
//! Drawn through the process's one imgui context (see [`crate::overlay`]), centred and scaled to
//! the display, in the workspace's shared theme (`er-overlay-theme`): a header bar with the title,
//! the names as the theme's list rows, the cursor's row selected. Three ways to choose, all live
//! at once:
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
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering};

use er_overlay_theme::{self as theme, Font, FrameFonts, Theme};
use hudhook::imgui::{Condition, Ui, WindowFlags, WindowHoveredFlags};
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

/// Window width at 1080p; the theme scales it with the display, as it does the text.
const WINDOW_WIDTH: f32 = 460.0;
/// The body's height at 1080p before it has been measured once.
const BODY_HEIGHT_GUESS: f32 = 240.0;
/// The body's height last frame, in screen pixels, as `f32` bits; 0 before the first.
static BODY_HEIGHT: AtomicU32 = AtomicU32::new(0);
/// The window's label. Only the part after `###` identifies it, and the theme's window has no
/// title bar, so the header draws [`TITLE`] instead.
const WINDOW_ID: &str = "Duel: choose an opponent###er-npc-summons-picker";
const TITLE: &str = "Duel: choose an opponent";

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

/// Draw the list onto a live imgui frame; a no-op while it is closed. Drawn in the theme at the
/// display's scale (`ui_scale`, its height over 1080) and in the `fonts` the host has built of the
/// ones the picker asked for the first time it was open.
pub(crate) fn draw(ui: &Ui, ui_scale: f32, fonts: &FrameFonts) {
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
    let scale = theme::panel_scale(ui_scale);
    let _theme = theme::enter(Theme::new(
        scale,
        fonts.table,
        fonts.granted,
        fonts.scale,
        theme::default_font_px(ui),
    ));
    // Asked once, the first time the picker is seen; the host's default font is drawn with until
    // they are built, this frame included.
    if let Some(handles) = theme::request_fonts(scale) {
        summons_log(format_args!(
            "picker: panel fonts: Plex Sans regular, medium and semibold and JetBrains Mono \
             handed to the overlay host at scale {scale}: handles {handles:?}. A 0 is a face the \
             host refused; a handle another panel already holds is that panel's fonts, shared"
        ));
    }
    let hovered = draw_window(ui, view);
    er_dinput_suppress_core::set_pointer_over_overlay(hovered || ui.io().want_capture_mouse);
}

/// The picker's window: the header, then the body in a padded region. True while the mouse is
/// over it.
fn draw_window(ui: &Ui, view: &mut View) -> bool {
    let t = theme::current();
    let [display_w, display_h] = ui.io().display_size;
    let width = t.px(WINDOW_WIDTH).round().min(display_w.max(1.0));
    let header = t.px(theme::HEADER_HEIGHT).round();
    let body = match f32::from_bits(BODY_HEIGHT.load(Ordering::Relaxed)) {
        measured if measured > 0.0 => measured,
        _ => t.px(BODY_HEIGHT_GUESS).round(),
    };
    let flags = WindowFlags::NO_TITLE_BAR
        | WindowFlags::NO_COLLAPSE
        | WindowFlags::NO_RESIZE
        | WindowFlags::NO_MOVE
        | WindowFlags::NO_SCROLLBAR
        | WindowFlags::NO_SCROLL_WITH_MOUSE;
    let window_style = theme::window_style(ui);
    let begun = ui
        .window(WINDOW_ID)
        .flags(flags)
        .position([display_w * 0.5, display_h * 0.4], Condition::Always)
        .position_pivot([0.5, 0.5])
        .size([width, header + t.stroke() + body], Condition::Always)
        .begin();
    drop(window_style);
    let Some(_window) = begun else {
        return false;
    };
    theme::apply_font_scale(ui);
    let _body = theme::font(ui, Font::Body);
    let _style = theme::panel_style(ui);
    let pos = ui.window_pos();
    let size = ui.window_size();
    draw_header(ui, pos, size[0]);
    ui.set_cursor_screen_pos([pos[0], pos[1] + header + t.stroke()]);
    theme::region(
        ui,
        "##er-npc-summons-picker-body",
        theme::CONTENT_PAD,
        [0.0, body],
        || {
            draw_body(ui, view);
            let measured =
                ui.item_rect_max()[1] - ui.window_pos()[1] + t.px(theme::CONTENT_PAD[1]).round();
            BODY_HEIGHT.store(measured.ceil().to_bits(), Ordering::Relaxed);
        },
    );
    theme::painter(ui).outline(pos, [pos[0] + size[0], pos[1] + size[1]], theme::RULE, 0.0);
    ui.is_window_hovered_with_flags(WindowHoveredFlags::ROOT_AND_CHILD_WINDOWS)
}

/// The header bar: the title, a rule under it.
fn draw_header(ui: &Ui, pos: [f32; 2], width: f32) {
    let t = theme::current();
    let height = t.px(theme::HEADER_HEIGHT).round();
    let [left, top] = pos;
    let right = left + width;
    let paint = theme::painter(ui);
    paint.with_clip([left, top], [right, top + height + t.stroke()], || {
        paint.fill([left, top], [right, top + height], theme::HEADER_BG, 0.0);
        paint.hline(left, right, top + height, theme::RULE);
    });
    let line = theme::line_height(ui, Font::Strong);
    ui.set_cursor_screen_pos([
        left + t.px(theme::HEADER_PAD_LEFT).round(),
        top + ((height - line) * 0.5).round(),
    ]);
    theme::text(ui, Font::Strong, theme::TEXT, TITLE);
}

/// Inside the padded region: the names, Cancel, and how to choose.
fn draw_body(ui: &Ui, view: &mut View) {
    {
        let _rows = theme::spacing(ui, theme::STACK_ROW_SPACING);
        for (index, name) in view.rows.iter().enumerate() {
            if name_row(ui, index, name, index == view.cursor) {
                view.cursor = index;
                CLICKED_ROW.store(index, Ordering::Release);
            }
            if ui.is_item_hovered() {
                view.cursor = index;
            }
        }
    }
    // The last row left the rows' spacing under it; make it the panel's.
    theme::gap(ui, theme::ITEM_SPACING[1] - theme::STACK_ROW_SPACING);
    theme::divider(ui);
    if theme::secondary_button(ui, "Cancel") {
        CLICKED_CANCEL.store(true, Ordering::Release);
    }
    theme::note(ui, "Click a name, or Up/Down and Enter, or d-pad and A.");
    theme::note(ui, "Backspace or B cancels.");
}

/// One name as a row across the content, the theme's selected row where the cursor is. True when
/// clicked.
fn name_row(ui: &Ui, index: usize, name: &str, selected: bool) -> bool {
    let t = theme::current();
    let at = ui.cursor_screen_pos();
    let width = ui.content_region_avail()[0].max(1.0);
    let height = t.px(theme::STACK_ROW_HEIGHT).round();
    let clicked = ui.invisible_button(format!("##duel-row-{index}"), [width, height]);
    let paint = theme::painter(ui);
    let (font, color) = if selected {
        paint.fill(
            at,
            [at[0] + width, at[1] + height],
            theme::SELECTED_ROW_BG,
            t.px(theme::FRAME_RADIUS),
        );
        (Font::Medium, theme::SELECTED_NAME)
    } else {
        (Font::Body, theme::TEXT)
    };
    theme::with_font(ui, font, || {
        let line = ui.text_line_height();
        paint.text(
            [
                at[0] + t.px(theme::STACK_ROW_PAD_X).round(),
                at[1] + ((height - line) * 0.5).round(),
            ],
            color,
            name,
        );
    });
    clicked
}
