//! The band census panel: a grid of every rune level band against every weapon level band, each
//! cell the number of hosts this player could invade there now, drawn in the shared theme
//! (`er-overlay-theme`) inside the overlay this DLL already joins.
//!
//! # What it shows
//!
//! A header with the title and a close button; one line that says what is being counted and how
//! fresh it is; the grid, shaded by how full each cell is against the fullest, with this
//! character's own band outlined; and the line that offers the other view. Hosts now is the view
//! the panel opens on. The view key (R by default, `band_census_view_key`) switches the grid to new
//! hosts per minute and back, and so does a click on that line. Hovering a cell names its band and
//! its number in words.
//!
//! # Input
//!
//! While it is open the panel takes the game's input through `er-overlay-input`: the cursor is free
//! and the camera holds still, and the mouse, the keys and the pad reach the panel, not the game.
//! The menu key (F8 by default, `band_census_key`) or Escape closes it. Keys count only while the
//! game's window has focus, so a key typed into another window never opens it.
//!
//! # Threads
//!
//! Everything here runs on the render thread inside `Present`. The grid comes from the view the
//! game task publishes (`band_census::publish`); nothing here touches Steam, the census or a file.

#![cfg(windows)]

use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU32, AtomicU64, Ordering};
use std::sync::{Mutex, PoisonError};

use er_invasion_warp_core::band_census::{
    BAND_COUNT, Cell, LEVEL_BANDS, WEAPON_BANDS, band_index, watched_text,
};
use er_invasion_warp_core::invade_difficulty::{level_band_labels, weapon_band_labels};
use er_invasion_warp_core::keybind::key_name;
use er_overlay_theme::{self as theme, Face, Font, FrameFonts, Icon, Span, Theme};
use hudhook::imgui::{Condition, Ui, WindowFlags};

use crate::band_census::{self, PanelView};
use crate::standalone_log as log;

/// The parts the panel records where it drew, for a pixel oracle.
pub(crate) const RECT_NAMES: [&str; 5] = ["panel", "header", "close_button", "grid", "view_prompt"];

/// The panel's width at 1080p: the label column, four cells and the padding either side.
const PANEL_WIDTH: f32 = 470.0;
/// The level label column's width at 1080p.
const LABEL_COLUMN: f32 = 92.0;
/// A cell's height at 1080p, and the gap between cells.
const CELL_HEIGHT: f32 = 26.0;
const CELL_GAP: f32 = 3.0;
/// The body's height at 1080p before it has been measured once.
const BODY_HEIGHT_GUESS: f32 = 420.0;
/// How much of the accent a full cell is shaded with.
const CELL_SHADE_ALPHA: f32 = 0.5;

static OPEN: AtomicBool = AtomicBool::new(false);
/// The grid shows new hosts per minute rather than hosts now. Reset to hosts now on every open.
static RATE_VIEW: AtomicBool = AtomicBool::new(false);
/// Set by a panic in the panel: it stays hidden for the rest of the session.
static HIDDEN: AtomicBool = AtomicBool::new(false);
/// Frames the panel was drawn open, and view switches.
static DRAWS: AtomicU64 = AtomicU64::new(0);
static VIEW_SWITCHES: AtomicU64 = AtomicU64::new(0);
/// The body's height last frame, in screen pixels, as `f32` bits; 0 before the first.
static BODY_HEIGHT: AtomicU32 = AtomicU32::new(0);
/// The host's handle for each face, as `request_fonts` returned them.
static FONT_HANDLES: Mutex<[u32; Face::COUNT]> = Mutex::new([0; Face::COUNT]);

/// Is the panel on screen?
#[must_use]
pub(crate) fn is_open() -> bool {
    OPEN.load(Ordering::Acquire)
}

fn set_open(open: bool) {
    if OPEN.swap(open, Ordering::AcqRel) == open {
        return;
    }
    // The settings panel shares this DLL's input capture, so closing this one while it is open
    // leaves the input taken.
    er_overlay_input::set_taking(open || crate::overlay::is_open());
    if open {
        RATE_VIEW.store(false, Ordering::Relaxed);
        band_census::arm();
    }
    log(format_args!(
        "band-panel: {}",
        if open { "opened" } else { "closed" }
    ));
}

fn switch_view() {
    let rate = !RATE_VIEW.load(Ordering::Relaxed);
    RATE_VIEW.store(rate, Ordering::Relaxed);
    VIEW_SWITCHES.fetch_add(1, Ordering::Relaxed);
}

/// After a panic in the panel's drawing: hide it for the rest of the session and give the game its
/// input back. The census keeps counting.
pub(crate) fn hide_for_session() {
    if !HIDDEN.swap(true, Ordering::AcqRel) {
        log(format_args!(
            "band-panel: a panic while drawing it; it stays hidden until the game restarts"
        ));
    }
    set_open(false);
}

#[link(name = "user32")]
unsafe extern "system" {
    fn GetAsyncKeyState(vkey: i32) -> i16;
}

const KEY_DOWN_MASK: u16 = 0x8000;
const VK_ESCAPE: i32 = 0x1b;

fn key_down(vk: i32) -> bool {
    // SAFETY: a plain query of the asynchronous key state.
    (unsafe { GetAsyncKeyState(vk) } as u16 & KEY_DOWN_MASK) != 0
}

/// One key's press edge. The latch remembers which key it is about, because a rebind would
/// otherwise inherit the old key's held state and swallow the new key's first press.
struct Edge {
    down: AtomicBool,
    key: AtomicI32,
}

impl Edge {
    const fn new() -> Self {
        Self {
            down: AtomicBool::new(false),
            key: AtomicI32::new(0),
        }
    }

    fn pressed(&self, vk: i32, focused: bool) -> bool {
        if self.key.swap(vk, Ordering::Relaxed) != vk {
            self.down.store(false, Ordering::Relaxed);
        }
        let down = focused && key_down(vk);
        let was = self.down.swap(down, Ordering::Relaxed);
        down && !was
    }
}

/// The menu key, the view key and Escape, edge-detected once per frame.
fn poll_keys(menu_key: i32, view_key: i32) {
    static MENU: Edge = Edge::new();
    static VIEW: Edge = Edge::new();
    static ESCAPE: Edge = Edge::new();
    let focused = crate::drive::game_has_focus();
    let menu = MENU.pressed(menu_key, focused);
    let view = VIEW.pressed(view_key, focused);
    let escape = ESCAPE.pressed(VK_ESCAPE, focused);
    if menu {
        set_open(!is_open());
    } else if escape && is_open() {
        set_open(false);
    } else if view && is_open() {
        switch_view();
    }
}

/// The panel's per-frame drawing, called by the overlay inside `catch_unwind`.
pub(crate) fn draw_ui(ui: &Ui, ui_scale: f32, fonts: &FrameFonts) {
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
    let (menu_key, view_key) = band_census::keys();
    poll_keys(menu_key, view_key);
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
        if let Some(handles) = theme::request_fonts(scale) {
            *FONT_HANDLES.lock().unwrap_or_else(PoisonError::into_inner) = handles;
            log(format_args!(
                "band-panel: fonts handed to the overlay host at scale {scale}: handles \
                 {handles:?}. A 0 is a face the host refused; a handle another panel already \
                 holds is shared"
            ));
        }
        draw_panel(ui, &key_name(menu_key), &key_name(view_key));
        DRAWS.fetch_add(1, Ordering::Relaxed);
    }
    theme::record(|record| {
        record.draws = DRAWS.load(Ordering::Relaxed);
        record.open = open;
        record.scale = scale;
        record.font_scale = frame_theme.font_scale;
        record.display = ui.io().display_size;
        record.set_flag("rate_view", RATE_VIEW.load(Ordering::Relaxed));
    });
    theme::publish_record();
}

/// The panel's window: the header, then the body in a padded region.
fn draw_panel(ui: &Ui, menu_key: &str, view_key: &str) {
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
        .window("Invadable hosts##er-invasion-warp-band-census")
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
    theme::apply_font_scale(ui);
    let _body = theme::font(ui, Font::Body);
    let _style = theme::panel_style(ui);
    let pos = ui.window_pos();
    let size = ui.window_size();
    theme::register_rect("panel", [pos[0], pos[1], size[0], size[1]]);
    draw_header(ui, pos, size[0]);
    ui.set_cursor_screen_pos([pos[0], pos[1] + header + t.stroke()]);
    let view = band_census::view();
    theme::region(
        ui,
        "##band-census-body",
        theme::CONTENT_PAD,
        [0.0, body],
        || {
            draw_body(ui, view.as_ref(), menu_key, view_key);
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
        theme::tooltip(ui, &["Close. The count carries on in the background."]);
    }
    let line = theme::line_height(ui, Font::Strong);
    ui.set_cursor_screen_pos([
        left + t.px(theme::HEADER_PAD_LEFT).round(),
        top + ((height - line) * 0.5).round(),
    ]);
    theme::text(ui, Font::Strong, theme::TEXT, "Invadable hosts");
}

/// What the line under the heading says about the count.
fn status_line(view: &PanelView, rate: bool) -> String {
    let progress = match view.progress {
        Some(band) => format!(" Counting band {} of {BAND_COUNT}.", band + 1),
        None => String::new(),
    };
    if rate {
        return format!(
            "New open hosts per minute, averaged over {} watched.{progress}",
            watched_text(view.watched_minutes)
        );
    }
    let whose = if view.pool_known {
        "in your matchmaking pool"
    } else {
        "in every pool"
    };
    format!(
        "{} open hosts {whose}, from the last full count.{progress}",
        view.total
    )
}

/// Inside the padded region: the heading, the state of the count, the grid, and the keys.
fn draw_body(ui: &Ui, view: Option<&PanelView>, menu_key: &str, view_key: &str) {
    let rate = RATE_VIEW.load(Ordering::Relaxed);
    theme::section_heading(
        ui,
        if rate {
            "New hosts per minute"
        } else {
            "Hosts now"
        },
    );
    let Some(view) = view else {
        theme::note(ui, "Starting the first count.");
        draw_footer(ui, menu_key, view_key, rate);
        return;
    };
    if !view.keys_known {
        theme::text_wrapped(
            ui,
            Font::Label,
            theme::WARNING_TEXT,
            "Not counting yet: Seamless renames its lobby keys every build, and this one has not \
             shown them. Starting a search or opening your world shows them.",
        );
        draw_footer(ui, menu_key, view_key, rate);
        return;
    }
    theme::note(ui, &status_line(view, rate));
    if !view.pool_known {
        theme::text_wrapped(
            ui,
            Font::Label,
            theme::WARNING_TEXT,
            "Seamless has not told Steam your matchmaking key yet, so these counts include hosts \
             your search cannot reach. Hosting or starting a search once fixes that.",
        );
    }
    if view.waiting_for_search {
        theme::text_wrapped(
            ui,
            Font::Label,
            theme::TEXT_QUIET,
            "A search is running, so counting waits for it: a count sent into a search would \
             cost Seamless its own answer.",
        );
    }
    theme::gap(ui, theme::CONTENT_GAP);
    let cells = if rate {
        view.rate.as_deref()
    } else {
        view.hosts.as_deref()
    };
    match cells {
        Some(cells) => draw_grid(ui, cells, view.own_band, rate),
        None => theme::note(
            ui,
            if rate {
                "No rate yet: it needs two full counts, about two minutes apart."
            } else {
                "The first count is still running."
            },
        ),
    }
    draw_footer(ui, menu_key, view_key, rate);
}

/// The grid: a row of weapon band labels, then one row per level band.
fn draw_grid(ui: &Ui, cells: &[Cell], own_band: Option<usize>, rate: bool) {
    let t = theme::current();
    let levels = level_band_labels();
    let weapons = weapon_band_labels();
    let origin = ui.cursor_screen_pos();
    let width = ui.content_region_avail()[0];
    let label_width = t.px(LABEL_COLUMN).round();
    let gap = t.px(CELL_GAP).round();
    let cell_width = ((width - label_width - gap * WEAPON_BANDS as f32) / WEAPON_BANDS as f32)
        .floor()
        .max(1.0);
    let cell_height = t.px(CELL_HEIGHT).round();
    let heading_height = theme::line_height(ui, Font::Label);
    let paint = theme::painter(ui);
    let column_x =
        |weapon: usize| origin[0] + label_width + gap + weapon as f32 * (cell_width + gap);
    let row_y =
        |level: usize| origin[1] + heading_height + gap + level as f32 * (cell_height + gap);
    theme::with_font(ui, Font::Label, || {
        for (weapon, label) in weapons.iter().enumerate() {
            let words = ui.calc_text_size(label)[0];
            paint.text(
                [
                    column_x(weapon) + ((cell_width - words) * 0.5).round(),
                    origin[1],
                ],
                theme::TEXT_QUIET,
                label,
            );
        }
        for (level, label) in levels.iter().enumerate() {
            let line = ui.calc_text_size(label)[1];
            paint.text(
                [
                    origin[0],
                    row_y(level) + ((cell_height - line) * 0.5).round(),
                ],
                theme::TEXT_SECONDARY,
                label,
            );
        }
    });
    let mut hovered = None;
    let accent = theme::ACCENT;
    theme::with_font(ui, Font::Mono, || {
        for level in 0..LEVEL_BANDS {
            for weapon in 0..WEAPON_BANDS {
                let band = band_index(level, weapon);
                let Some(cell) = cells.get(band) else {
                    continue;
                };
                let min = [column_x(weapon), row_y(level)];
                let max = [min[0] + cell_width, min[1] + cell_height];
                paint.fill(min, max, theme::INPUT_BG, t.px(theme::FRAME_RADIUS));
                if cell.weight > 0.0 {
                    let shade = [
                        accent[0],
                        accent[1],
                        accent[2],
                        cell.weight * CELL_SHADE_ALPHA,
                    ];
                    paint.fill(min, max, shade, t.px(theme::FRAME_RADIUS));
                }
                let own = own_band == Some(band);
                paint.outline(
                    min,
                    max,
                    if own { theme::ACCENT } else { theme::RULE },
                    t.px(theme::FRAME_RADIUS),
                );
                let words = ui.calc_text_size(&cell.text);
                let color = if cell.weight > 0.0 {
                    theme::TEXT
                } else {
                    theme::TEXT_GUTTER
                };
                paint.text(
                    [
                        min[0] + ((cell_width - words[0]) * 0.5).round(),
                        min[1] + ((cell_height - words[1]) * 0.5).round(),
                    ],
                    color,
                    &cell.text,
                );
                if ui.is_mouse_hovering_rect(min, max) {
                    hovered = Some((level, weapon, cell.text.clone(), own));
                }
            }
        }
    });
    let height = heading_height + gap + LEVEL_BANDS as f32 * (cell_height + gap);
    ui.set_cursor_screen_pos(origin);
    ui.dummy([width, height]);
    theme::register_rect("grid", [origin[0], origin[1], width, height]);
    if let Some((level, weapon, text, own)) = hovered {
        let place = format!("{}, {}", levels[level], weapons[weapon]);
        let what = if rate {
            format!("{text} new open hosts a minute.")
        } else {
            format!("{text} open hosts now.")
        };
        let mut lines = vec![place, what];
        if own {
            lines.push("Your own band: the one your searches ask for first.".to_owned());
        }
        theme::tooltip(ui, &lines);
    }
}

/// The view prompt, clickable, and the keys that close the panel.
fn draw_footer(ui: &Ui, menu_key: &str, view_key: &str, rate: bool) {
    theme::gap(ui, theme::CONTENT_GAP);
    let other = if rate {
        " to see hosts now"
    } else {
        " to see new hosts per minute"
    };
    let prompt = [
        Span::Quiet("Press "),
        Span::Key(view_key),
        Span::Quiet(other),
    ];
    let at = ui.cursor_screen_pos();
    theme::spans(ui, &prompt);
    let size = [
        theme::spans_width(ui, &prompt),
        theme::line_height(ui, Font::Label),
    ];
    // The line is a button as well as a hint, so a player with the mouse in hand need not reach
    // for the key. The invisible button sits over the line already drawn and takes its clicks.
    let after = ui.cursor_screen_pos();
    ui.set_cursor_screen_pos(at);
    if ui.invisible_button("##band-census-view", [size[0].max(1.0), size[1].max(1.0)]) {
        switch_view();
    }
    let hovered = ui.is_item_hovered();
    theme::register_item(ui, "view_prompt");
    ui.set_cursor_screen_pos(after);
    if hovered {
        theme::tooltip(ui, &[format!("Click, or press {view_key}.")]);
    }
    theme::gap(ui, theme::TOOLBAR_ROW_GAP);
    theme::spans(
        ui,
        &[
            Span::Key(menu_key),
            Span::Quiet(" or "),
            Span::Key("Esc"),
            Span::Quiet(" closes. Keys are set in er-invasion-warp.toml."),
        ],
    );
}

/// The panel's counters for the heartbeat line.
#[must_use]
pub(crate) fn counters() -> (bool, u64, u64) {
    (
        is_open(),
        DRAWS.load(Ordering::Relaxed),
        VIEW_SWITCHES.load(Ordering::Relaxed),
    )
}
