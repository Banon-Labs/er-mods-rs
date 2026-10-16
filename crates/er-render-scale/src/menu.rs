//! The Render scale menu: a small panel the menu key (F5 by default) opens and hides, drawn in the
//! shared theme (`er-overlay-theme`) as a guest of the process's one imgui overlay.
//!
//! # What it shows
//!
//! A header with the title and a close button; a slider that steps through exactly the clean
//! divisors of the screen the game draws to, each step labelled with the scene size it draws
//! (`1280×720`), recomputed whenever the output size changes; the size the game actually drew,
//! read back out of its own viewport ratio, with a light that says whether it is the size asked
//! for; the game's own GPU time; a Native button; and the keys that close it. When the draw context
//! carries two output sizes that disagree, or the update could not be hooked, the slider is replaced
//! by the reason.
//!
//! # Input
//!
//! While it is open the menu takes the game's input through `er-overlay-input`: the cursor is free
//! and the camera holds still, and the mouse, the keys and the pad reach the menu, not the game.
//! The menu key or Escape closes it.
//!
//! # Threads
//!
//! Everything here runs on the render thread inside the overlay host's `Present`. Moving the slider
//! puts the divisor in force at once (`config::set_divisor`); the file is written by the worker.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Mutex, PoisonError};

use er_hotkey_config::keys::{MODIFIER_ALT, MODIFIER_CTRL, MODIFIER_SHIFT};
use er_hotkey_config::{Chord, chord_name};
use er_overlay_theme::{
    self as theme, Face, Font, FontState, FrameFonts, Icon, Light, SliderRow, Span, Theme,
};
use hudhook::imgui::{Condition, Ui, WindowFlags};
use windows::Win32::System::Threading::GetCurrentProcessId;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, VK_CONTROL, VK_ESCAPE, VK_MENU, VK_SHIFT,
};
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

use crate::report::{self, MenuState, View};
use crate::scale::{self, NATIVE};
use crate::{config, drs, log, settings};

/// The parts the panel records where it drew, for a pixel oracle.
pub const RECT_NAMES: [&str; 6] = [
    "panel",
    "header",
    "close_button",
    "slider",
    "drawn",
    "native_button",
];

/// The panel's width at 1080p.
const PANEL_WIDTH: f32 = 380.0;
/// The body's height at 1080p before it has been measured once.
const BODY_HEIGHT_GUESS: f32 = 260.0;
/// The slider row: a short label, and a value column wide enough for `3840×2160` in mono.
const SCALE_ROW: SliderRow = SliderRow {
    label_width: 44.0,
    label_colour: theme::TEXT_SECONDARY,
    value_width: 84.0,
    gap: 8.0,
};

static OPEN: AtomicBool = AtomicBool::new(false);
/// Set by a panic in the panel: it stays hidden for the rest of the session.
static HIDDEN: AtomicBool = AtomicBool::new(false);
/// Frames the panel was drawn open.
static DRAWS: AtomicU64 = AtomicU64::new(0);
/// The body's height last frame, in screen pixels, as `f32` bits; 0 before the first.
static BODY_HEIGHT: AtomicU32 = AtomicU32::new(0);
/// The host's handle for each face, as `request_fonts` returned them.
static FONT_HANDLES: Mutex<[u32; Face::COUNT]> = Mutex::new([0; Face::COUNT]);

/// The menu key in force, and how the file spells it.
static MENU_KEY: Mutex<Option<(Chord, String)>> = Mutex::new(None);

/// Put `spelling` in force as the menu key; a spelling that is not a key keeps F5.
pub fn set_menu_key(spelling: &str) {
    let chord = settings::menu_chord(spelling);
    *MENU_KEY.lock().unwrap_or_else(PoisonError::into_inner) = Some((chord, chord_name(chord)));
}

fn menu_key() -> (Chord, String) {
    MENU_KEY
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .clone()
        .unwrap_or_else(|| {
            let chord = settings::menu_chord(settings::DEFAULT_MENU_KEY);
            (chord, chord_name(chord))
        })
}

/// Is the panel on screen?
#[must_use]
pub fn is_open() -> bool {
    OPEN.load(Ordering::Acquire)
}

fn set_open(open: bool) {
    if OPEN.swap(open, Ordering::AcqRel) != open {
        er_overlay_input::set_taking(open);
        log(format_args!(
            "menu {}",
            if open { "opened" } else { "closed" }
        ));
    }
}

/// After a panic in the panel's drawing: hide it for the rest of the session and give the game its
/// input back. The divisor in force stays in force.
pub fn hide_for_session() {
    if !HIDDEN.swap(true, Ordering::AcqRel) {
        log(format_args!(
            "menu: a panic while drawing it; it stays hidden until the game restarts, and the \
             scale in force stays in force"
        ));
    }
    set_open(false);
}

fn key_down(vk: u32) -> bool {
    // SAFETY: a plain query of the asynchronous key state.
    (unsafe { GetAsyncKeyState(vk as i32) } as u16 & 0x8000) != 0
}

/// Is every key of `chord` down? Extra modifiers do not stop it.
fn chord_held(chord: Chord) -> bool {
    let modifier = |bit: u8, vk: u16| chord.modifiers & bit == 0 || key_down(u32::from(vk));
    modifier(MODIFIER_CTRL, VK_CONTROL.0)
        && modifier(MODIFIER_ALT, VK_MENU.0)
        && modifier(MODIFIER_SHIFT, VK_SHIFT.0)
        && key_down(chord.vk)
}

/// Does the foreground window belong to the game? `GetAsyncKeyState` sees keys pressed in every
/// application, and a key typed into a browser must not open the panel.
fn game_has_focus() -> bool {
    // SAFETY: plain window queries; a null window yields process id 0.
    unsafe {
        let window = GetForegroundWindow();
        let mut process = 0;
        GetWindowThreadProcessId(window, Some(&mut process));
        process == GetCurrentProcessId()
    }
}

/// The menu key and Escape, edge-detected once per frame.
fn poll_keys(chord: Chord) {
    static KEY_WAS_DOWN: AtomicBool = AtomicBool::new(false);
    static ESCAPE_WAS_DOWN: AtomicBool = AtomicBool::new(false);
    let focused = game_has_focus();
    let key = focused && chord_held(chord);
    let escape = focused && key_down(u32::from(VK_ESCAPE.0));
    let key_pressed = key && !KEY_WAS_DOWN.swap(key, Ordering::Relaxed);
    if !key {
        KEY_WAS_DOWN.store(false, Ordering::Relaxed);
    }
    let escape_pressed = escape && !ESCAPE_WAS_DOWN.swap(escape, Ordering::Relaxed);
    if !escape {
        ESCAPE_WAS_DOWN.store(false, Ordering::Relaxed);
    }
    if key_pressed {
        set_open(!is_open());
    } else if escape_pressed && is_open() {
        set_open(false);
    }
}

/// The panel's per-frame drawing, called by the overlay inside `catch_unwind`: the keys that open
/// and close it, and the panel itself while it is open, in the `fonts` the host has built of the
/// ones the panel asked for the first time it was drawn. Each frame ends by publishing what the
/// panel drew where.
pub fn draw_ui(ui: &Ui, ui_scale: f32, fonts: &FrameFonts) {
    static ARMED: AtomicBool = AtomicBool::new(false);
    theme::set_rect_names(&RECT_NAMES);
    theme::begin_record();
    if HIDDEN.load(Ordering::Acquire) {
        theme::record(|record| record.open = false);
        theme::publish_record();
        return;
    }
    if !ARMED.load(Ordering::Relaxed) {
        // Retried each frame until DirectInput and XInput are loaded; cheap once both are.
        let dinput = er_overlay_input::try_install_dinput(log);
        let pad = er_overlay_input::try_install_pad(log);
        ARMED.store(dinput && pad, Ordering::Relaxed);
    }
    let (chord, key_name) = menu_key();
    poll_keys(chord);
    let open = is_open();
    let scale = theme::panel_scale(ui_scale);
    let frame_theme = Theme::new(
        scale,
        fonts.table,
        fonts.granted,
        fonts.scale,
        theme::default_font_px(ui),
    );
    let _theme = theme::enter(frame_theme);
    if open {
        // Asked once, the first time the panel is seen; the host's default font is drawn with
        // until they are built, this frame included.
        if let Some(handles) = theme::request_fonts(scale) {
            *FONT_HANDLES.lock().unwrap_or_else(PoisonError::into_inner) = handles;
            log(format_args!(
                "panel fonts: Plex Sans regular, medium and semibold and JetBrains Mono handed to \
                 the overlay host at scale {scale}: handles {handles:?}. A 0 is a face the host \
                 refused; a handle another panel already holds is that panel's fonts, shared"
            ));
        }
        draw_panel(ui, &key_name);
        DRAWS.fetch_add(1, Ordering::Relaxed);
    }
    theme::record(|record| {
        record.draws = DRAWS.load(Ordering::Relaxed);
        record.open = open;
        record.scale = scale;
        record.font_scale = frame_theme.font_scale;
        record.display = ui.io().display_size;
        record.fonts = FontState {
            requested: theme::fonts_requested(),
            request_scale: fonts.scale.unwrap_or(0.0),
            handles: fonts.handles,
            granted: fonts.granted,
            in_use: frame_theme.custom_fonts(),
        };
    });
    theme::publish_record();
}

/// The panel's window: the header, then the body in a padded region.
fn draw_panel(ui: &Ui, key_name: &str) {
    let t = theme::current();
    let display = ui.io().display_size;
    let width = (PANEL_WIDTH * t.scale).round().min(display[0].max(1.0));
    let header = t.px(theme::HEADER_HEIGHT).round();
    let body = match f32::from_bits(BODY_HEIGHT.load(Ordering::Relaxed)) {
        measured if measured > 0.0 => measured,
        _ => t.px(BODY_HEIGHT_GUESS).round(),
    };
    let flags = WindowFlags::NO_TITLE_BAR
        | WindowFlags::NO_COLLAPSE
        | WindowFlags::NO_SAVED_SETTINGS
        | WindowFlags::NO_SCROLLBAR
        | WindowFlags::NO_SCROLL_WITH_MOUSE
        | WindowFlags::NO_RESIZE;
    let window_style = theme::window_style(ui);
    let begun = ui
        .window("Render scale##er-render-scale")
        .flags(flags)
        .position(
            [
                ((display[0] - width) * 0.5).round(),
                (display[1] * 0.12).round(),
            ],
            Condition::FirstUseEver,
        )
        .size([width, header + t.stroke() + body], Condition::Always)
        .begin();
    drop(window_style);
    let Some(_window) = begun else {
        return;
    };
    ui.set_window_font_scale(t.font_scale);
    let _body = theme::font(ui, Font::Body);
    let _style = theme::panel_style(ui);
    let pos = ui.window_pos();
    let size = ui.window_size();
    theme::register_rect("panel", [pos[0], pos[1], size[0], size[1]]);
    draw_header(ui, pos, size[0]);
    ui.set_cursor_screen_pos([pos[0], pos[1] + header + t.stroke()]);
    let readings = drs::read();
    let hook = drs::hook_state();
    let view = report::view(drs::selected(), drs::applied(), &readings);
    theme::region(
        ui,
        "##render-scale-body",
        theme::CONTENT_PAD,
        [0.0, body],
        || {
            draw_body(ui, &view, &hook, readings.gpu_ms, key_name);
            let measured = ui.cursor_pos()[1] + t.px(theme::CONTENT_PAD[1]).round();
            BODY_HEIGHT.store(measured.ceil().to_bits(), Ordering::Relaxed);
        },
    );
    theme::painter(ui).outline(pos, [pos[0] + size[0], pos[1] + size[1]], theme::RULE, 0.0);
}

/// The header bar: the title on the left, close on the right, a rule under it.
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
    theme::register_rect("header", [left, top, width, height]);
    let icon = t.px(theme::ICON_BUTTON).round();
    ui.set_cursor_screen_pos([
        right - t.px(theme::HEADER_PAD_RIGHT).round() - icon,
        top + ((height - icon) * 0.5).round(),
    ]);
    if theme::icon_button(ui, "##close", Icon::Close, theme::ICON_BUTTON) {
        set_open(false);
    }
    theme::register_item(ui, "close_button");
    if ui.is_item_hovered() {
        theme::tooltip(ui, &["Close. The scale stays in force."]);
    }
    let line = theme::line_height(ui, Font::Strong);
    ui.set_cursor_screen_pos([
        left + t.px(theme::HEADER_PAD_LEFT).round(),
        top + ((height - line) * 0.5).round(),
    ]);
    theme::text(ui, Font::Strong, theme::TEXT, "Render scale");
}

/// The light beside the size the game drew.
fn drawn_light(view: &View, installed: bool) -> Light {
    if !installed {
        Light::Error
    } else if !view.exact {
        Light::Busy
    } else if view.applied > NATIVE {
        Light::Live
    } else {
        Light::Off
    }
}

/// Inside the padded region: the slider or the reason there is none, what the game drew, its GPU
/// time, Native, and the keys that close the panel.
fn draw_body(ui: &Ui, view: &View, hook: &report::HookState, gpu_ms: Option<f32>, key_name: &str) {
    theme::section_heading(ui, "Scene resolution");
    match view.offered {
        Err(reason) => {
            theme::text_wrapped(
                ui,
                Font::Label,
                theme::WARNING_TEXT,
                &format!("No scale is offered: {reason}."),
            );
        }
        Ok(output) => {
            let divisors = &view.divisors;
            let last = i32::try_from(divisors.len().saturating_sub(1)).unwrap_or(0);
            let mut index = i32::try_from(scale::index_of(divisors, view.selected)).unwrap_or(0);
            let shown = divisors.get(index as usize).copied().unwrap_or(NATIVE);
            let label = scale::size_text(scale::scene_size(output, shown));
            if SCALE_ROW.draw_int(ui, "Scale##scale", &mut index, [0, last], &label)
                && let Some(&divisor) = divisors.get(index.max(0) as usize)
            {
                config::set_divisor(divisor);
            }
            theme::register_rect("slider", theme::last_track());
            let words = if shown == NATIVE {
                format!(
                    "Native: the scene is drawn at the screen's {}.",
                    scale::size_text(output)
                )
            } else {
                format!(
                    "1/{shown} of {}: each scene pixel covers {shown}\u{d7}{shown} screen pixels.",
                    scale::size_text(output)
                )
            };
            theme::note(ui, &words);
            if view.selected != shown {
                theme::text_wrapped(
                    ui,
                    Font::Label,
                    theme::WARNING_TEXT,
                    &format!(
                        "The saved divisor {} does not divide this screen into whole pixels, so \
                         the scene is drawn at native until a step is chosen.",
                        view.selected
                    ),
                );
            }
        }
    }
    if let Some(refusal) = hook.refusal.as_deref() {
        theme::text_wrapped(
            ui,
            Font::Label,
            theme::ERROR_TEXT,
            &format!("Not active: {refusal}."),
        );
    }
    theme::gap(ui, theme::SECTION_GAP);
    theme::section_heading(ui, "Drawn by the game");
    let mut row = theme::Row::begin(ui, theme::FRAME_HEIGHT, theme::DOT_GAP);
    let dot = theme::px(theme::DOT_SIZE);
    let height = ui.frame_height();
    row.next(ui, [dot, height]);
    theme::light_dot(ui, drawn_light(view, hook.installed));
    let drawn = view.drawn.map_or_else(|| "--".to_owned(), scale::size_text);
    let drawn_size = [
        theme::text_width(ui, Font::Mono, &drawn),
        theme::line_height(ui, Font::Mono),
    ];
    row.next(ui, drawn_size);
    theme::mono(ui, &drawn);
    let status = match (view.drawn, view.expected) {
        (None, _) => "no reading yet".to_owned(),
        (Some(_), _) if view.exact => "as asked".to_owned(),
        (Some(_), Some(expected)) => format!("asked for {}", scale::size_text(expected)),
        (Some(_), None) => "no output size".to_owned(),
    };
    let status_size = [
        theme::text_width(ui, Font::Label, &status),
        theme::line_height(ui, Font::Label),
    ];
    row.next(ui, status_size);
    theme::quiet(ui, &status);
    let drawn_rect = row.rect();
    row.end(ui);
    theme::register_rect("drawn", drawn_rect);
    let gpu = gpu_ms.map_or_else(
        || "GPU time not reported yet".to_owned(),
        |ms| format!("GPU {ms:.2} ms"),
    );
    theme::mono_quiet(ui, &gpu);
    if view.selected != NATIVE || view.applied != NATIVE {
        theme::gap(ui, theme::CONTENT_GAP);
        if theme::secondary_button(ui, "Native##native") {
            config::set_divisor(NATIVE);
        }
        theme::register_item(ui, "native_button");
        if ui.is_item_hovered() {
            theme::tooltip(ui, &["Draw the scene at the screen's own size again."]);
        }
    }
    theme::gap(ui, theme::CONTENT_GAP);
    theme::spans(
        ui,
        &[
            Span::Key(key_name),
            Span::Quiet(" or "),
            Span::Key("Esc"),
            Span::Quiet(" closes. Saved to er-render-scale.toml."),
        ],
    );
}

/// The menu's part of the telemetry.
#[must_use]
pub fn state() -> MenuState {
    MenuState {
        open: is_open(),
        menu_key: menu_key().1,
        input: er_overlay_input::counters(),
        record: theme::last_record(),
        font_handles: *FONT_HANDLES.lock().unwrap_or_else(PoisonError::into_inner),
        fonts_requested: theme::fonts_requested(),
    }
}
