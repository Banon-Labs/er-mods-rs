//! Drawing the creature list.
//!
//! The present hook is not here. This module used to own the host-join as well, back when the
//! picker was the only thing this DLL drew; that half moved to [`crate::overlay`] when the
//! attack-set panel arrived, because two surfaces must share one `Present` hook and neither of
//! them should own the other's install. What is left here is the panel itself: given a live imgui
//! frame, paint the list if it is open.
//!
//! It draws in the workspace's shared theme (`er-overlay-theme`), which [`crate::overlay`] enters
//! for the frame: a header bar with the title and the cursor's place in the list, then the rows in
//! JetBrains Mono, because a row lines its columns up with spaces.
//!
//! # `PANEL_DRAWS` and the reading it exists to prevent
//!
//! [`draw`] is called on every frame and returns immediately when the list is closed, which is
//! almost all of them. The counter therefore has to be incremented after that early return, or it
//! measures the swapchain rather than the picker -- which is exactly the confusion that made a
//! working picker look broken for a day. See the module docs of [`crate::overlay`] for the log
//! evidence.

#![cfg(windows)]

use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

use er_overlay_theme::{self as theme, Font};
use hudhook::imgui::{Condition, Ui, WindowFlags};

use crate::picker::View;
use crate::picker::catalog::LABEL_MAX_CHARS;

/// Frames the list itself was built on -- not frames the overlay drew. Zero while the picker has
/// been open is the fault worth chasing; zero while it is closed is the picker being closed.
static PANEL_DRAWS: AtomicUsize = AtomicUsize::new(0);
/// Rows painted on the most recent draw.
static LAST_ROWS: AtomicUsize = AtomicUsize::new(0);
/// The body's height last frame, in screen pixels, as `f32` bits; 0 before the first.
static BODY_HEIGHT: AtomicU32 = AtomicU32::new(0);

/// Where the panel sits. First-use only -- the window is movable, so a player who drags it keeps
/// it there.
const PANEL_POSITION: [f32; 2] = [48.0, 96.0];
/// The panel's width at 1080p, scaled with the theme like its text. A row is at most 61
/// characters of JetBrains Mono 12, 7.2 pixels each: the cursor mark, a name clipped to
/// `LABEL_MAX_CHARS`, the id and a shape of four three-digit buckets. That is 439 pixels, and this
/// leaves room for them inside the content padding, so a name is never clipped by the panel.
const PANEL_WIDTH: f32 = 480.0;
/// The body's height at 1080p before it has been measured once.
const BODY_HEIGHT_GUESS: f32 = 440.0;

/// The window's label: everything after `###` is its ID and none of it is drawn. It must be
/// stable, because imgui derives a window's identity from its label, and a new identity is a new
/// window with its default place and size.
const PANEL_ID: &str = "###er-npc-possess-picker";
/// The title in the header bar.
const TITLE: &str = "possess: pick a creature";

pub(crate) fn panel_draws() -> usize {
    PANEL_DRAWS.load(Ordering::Relaxed)
}

pub(crate) fn last_rows() -> usize {
    LAST_ROWS.load(Ordering::Relaxed)
}

/// Draw the current view onto a live imgui frame, in the theme [`crate::overlay`] entered. A no-op
/// while the list is closed.
pub(crate) fn draw(ui: &Ui) {
    // Lock-free fast path, and this is the common one: once the overlay is installed this runs on
    // every `Present` for the rest of the process, and the list is closed for almost all of them.
    // Taking the picker mutex 60-144 times a second to be told "closed" would contend with the
    // game thread's own per-frame tick for nothing.
    if !crate::picker::is_drawing() {
        LAST_ROWS.store(0, Ordering::Relaxed);
        return;
    }
    let Some(view) = crate::picker::view() else {
        LAST_ROWS.store(0, Ordering::Relaxed);
        return;
    };
    PANEL_DRAWS.fetch_add(1, Ordering::Relaxed);
    LAST_ROWS.store(view.rows.len(), Ordering::Relaxed);
    crate::overlay::request_theme_fonts();
    let t = theme::current();
    let display = ui.io().display_size;
    let width = t.px(PANEL_WIDTH).round().min(display[0].max(1.0));
    let header = t.px(theme::HEADER_HEIGHT).round();
    let body = match f32::from_bits(BODY_HEIGHT.load(Ordering::Relaxed)) {
        measured if measured > 0.0 => measured,
        _ => t.px(BODY_HEIGHT_GUESS).round(),
    };
    let flags = WindowFlags::NO_TITLE_BAR
        | WindowFlags::NO_COLLAPSE
        | WindowFlags::NO_RESIZE
        | WindowFlags::NO_SCROLLBAR
        | WindowFlags::NO_SCROLL_WITH_MOUSE;
    let window_style = theme::window_style(ui);
    let begun = ui
        .window(PANEL_ID)
        .flags(flags)
        .position(PANEL_POSITION, Condition::FirstUseEver)
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
    draw_header(ui, pos, size[0], &view);
    ui.set_cursor_screen_pos([pos[0], pos[1] + header + t.stroke()]);
    theme::region(
        ui,
        "##er-npc-possess-picker-body",
        theme::CONTENT_PAD,
        [0.0, body],
        || {
            draw_rows(ui, &view);
            let measured =
                ui.item_rect_max()[1] - ui.window_pos()[1] + t.px(theme::CONTENT_PAD[1]).round();
            BODY_HEIGHT.store(measured.ceil().to_bits(), Ordering::Relaxed);
        },
    );
    theme::painter(ui).outline(pos, [pos[0] + size[0], pos[1] + size[1]], theme::RULE, 0.0);
}

/// The header bar: the title on the left, the cursor's place in the list on the right (`12/408`),
/// a rule under it.
fn draw_header(ui: &Ui, pos: [f32; 2], width: f32, view: &View) {
    let t = theme::current();
    let height = t.px(theme::HEADER_HEIGHT).round();
    let pad = t.px(theme::HEADER_PAD_LEFT).round();
    let [left, top] = pos;
    let right = left + width;
    let paint = theme::painter(ui);
    paint.with_clip([left, top], [right, top + height + t.stroke()], || {
        paint.fill([left, top], [right, top + height], theme::HEADER_BG, 0.0);
        paint.hline(left, right, top + height, theme::RULE);
    });
    let place = format!("{}/{}", view.position, view.total);
    let place_line = theme::line_height(ui, Font::Mono);
    ui.set_cursor_screen_pos([
        right - pad - theme::text_width(ui, Font::Mono, &place),
        top + ((height - place_line) * 0.5).round(),
    ]);
    theme::text(ui, Font::Mono, theme::TEXT_QUIET, &place);
    let line = theme::line_height(ui, Font::Strong);
    ui.set_cursor_screen_pos([left + pad, top + ((height - line) * 0.5).round()]);
    theme::text(ui, Font::Strong, theme::TEXT, TITLE);
}

fn draw_rows(ui: &Ui, view: &View) {
    for row in &view.rows {
        let creature = &row.creature;
        // The id is always shown beside the name. A player who already knows they want `c4500`
        // should not have to know it is called Flying Dragon, and the id is what goes in the
        // config file.
        let line = format!(
            "{}{:<width$} c{:04}  {}",
            if row.selected { "> " } else { "  " },
            creature.clipped_label(),
            creature.chr_id,
            creature.shape(),
            width = LABEL_MAX_CHARS,
        );
        if row.selected {
            selected_backing(ui);
            theme::text(ui, Font::Mono, theme::SELECTED_NAME, &line);
        } else if creature.is_mute() {
            // Dimmed rather than hidden: becoming one gets you a body that cannot attack, and
            // that is worth seeing in the list rather than after the possession.
            theme::text(ui, Font::Mono, theme::TEXT_GUTTER, &line);
        } else {
            theme::mono(ui, &line);
        }
    }
    theme::divider(ui);
    let summary = match &view.selected {
        // A mute creature in the shipped table has zero moves and zero denials -- it is a variant
        // that owns a model but declares no animations of its own, so nothing was classified
        // rather than everything being withheld. Saying "0 animations were considered and all
        // withheld" was both self-contradictory and backwards.
        Some(creature) if creature.is_mute() => format!(
            "c{:04} has no fireable move -- this variant declares no animations of its own",
            creature.chr_id
        ),
        Some(creature) => format!(
            "c{:04}: {} moves, {} withheld  |  light {} heavy {} ranged {} movement {}",
            creature.chr_id,
            creature.moves,
            creature.denials,
            creature.buckets[0],
            creature.buckets[1],
            creature.buckets[2],
            creature.buckets[3],
        ),
        None => "no creatures -- the shipped moveset table is empty".to_owned(),
    };
    theme::text_wrapped(ui, Font::Mono, theme::TEXT, &summary);
    theme::note(
        ui,
        "press your POSSESS hotkey to choose, the picker hotkey to close",
    );
}

/// The theme's selected-row background behind the line about to be drawn at the cursor: the line's
/// height and half the gap to each neighbour, across the content and a row's padding past it.
fn selected_backing(ui: &Ui) {
    let t = theme::current();
    let at = ui.cursor_screen_pos();
    let line = theme::line_height(ui, Font::Mono);
    let half_gap = (t.px(theme::ITEM_SPACING[1]) * 0.5).round();
    let inset = t.px(theme::STACK_ROW_PAD_X).round();
    let min = [at[0] - inset, at[1] - half_gap];
    let max = [
        at[0] + ui.content_region_avail()[0] + inset,
        at[1] + line + half_gap,
    ];
    let paint = theme::painter(ui);
    paint.with_clip(min, max, || {
        paint.fill(min, max, theme::SELECTED_ROW_BG, t.px(theme::FRAME_RADIUS));
    });
}
