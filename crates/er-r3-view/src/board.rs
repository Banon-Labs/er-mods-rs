//! The weapon board drawn in view 3: the canvas "Weapon: what makes it good" laid out in imgui, in
//! the workspace's shared look (`er-overlay-theme`).
//!
//! Positions and gaps are the canvas's own, in its 1440 x 1200 frame, stretched edge to edge:
//! across by the screen's width, down by its height, so the columns keep their place beside the
//! game's item list at every resolution. Type is the theme's: each kind of text is set in the
//! theme [`Font`] nearest its canvas size, at the panel's scale, and its colour is a theme token.
//! What depends on a size of type -- a key's text centred on its line, a gear icon as tall as two
//! lines -- is measured from the theme font's line height, not from the canvas size.
//!
//! Every board is generated from the mechanics scripts into `weapon_boards.rs` by
//! `scripts/gen-r3-weapon-boards.py`.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use er_overlay_theme::{self as theme, Font};
use hudhook::imgui::sys;
use hudhook::imgui::{Condition, TextureId, Ui, WindowFlags};

pub struct Line {
    pub key: &'static str,
    pub text: &'static str,
}

pub struct Gear {
    /// The item's `iconId` (`EquipParamAccessory`, `EquipParamProtector` `iconIdM` or
    /// `EquipParamWeapon`); the powerstance row carries the weapon's own.
    pub icon_id: u32,
    pub name: &'static str,
    pub text: &'static str,
}

/// What the board adds to the game's item list. The weapon's name, class, icon and whether it can be
/// infused are the list's own to show, in the panel left of the board.
pub struct Board {
    pub unique_intro: &'static str,
    pub unique: &'static [Line],
    /// "Top Infusions", or "Ash of War" for a weapon with its own skill or no infusion.
    pub infusions_heading: &'static str,
    pub infusions_intro: &'static str,
    pub infusions: &'static [Line],
    pub speed: &'static [Line],
    /// How much of the moveset a defender parries, and the multi-swing attacks a roll then a
    /// parry catches (`er-mechanics-crits.parryability`, `parry_follow_up`).
    pub parry_intro: &'static str,
    pub parry: &'static [Line],
    pub gear: &'static [Gear],
}

/// The board for an `EquipParamWeapon` row id as the item list carries it, infusion and upgrade
/// level included: the row's base weapon is the id rounded down to a multiple of 10000.
pub fn for_weapon(id: u32) -> Option<&'static Board> {
    let base = id - id % WEAPON_ID_BASE_STEP;
    crate::weapon_boards::WEAPON_BOARDS
        .binary_search_by_key(&base, |(id, _)| *id)
        .ok()
        .map(|at| &crate::weapon_boards::WEAPON_BOARDS[at].1)
}

/// Infusion is `id % 10000 / 100` and the upgrade level `id % 100`, so the base weapon of any row
/// is its id rounded down to this.
const WEAPON_ID_BASE_STEP: u32 = 10000;

/// What view 3 shows for a weapon the generator left out: its header names why.
pub const NO_BOARD: Board = Board {
    unique_intro: "No board for this weapon: the generator could not evaluate it, and the header of \
                   weapon_boards.rs names why.",
    unique: &[],
    infusions_heading: "Top Infusions",
    infusions_intro: "",
    infusions: &[],
    speed: &[],
    parry_intro: "",
    parry: &[],
    gear: &[],
};

const FRAME_W: f32 = 1440.0;
const FRAME_H: f32 = 1200.0;
/// The game's own item list, which view 3 leaves up: its panel ends at 0.36 of the screen's width
/// (1280 of 1347 px in a full-height capture of a 3840-wide screen at 0.926 scale, 2026-10-04), so
/// the board starts a little right of it. The weapon's name and icon are the game's to show there.
const GAME_PANEL_W: f32 = 540.0;
const PAD: f32 = 56.0;
const COL_GAP: f32 = 48.0;
const COL1_W: f32 = (FRAME_W - GAME_PANEL_W - 2.0 * PAD - COL_GAP) * 1.25 / 2.25;
const COL1_X: f32 = GAME_PANEL_W + PAD;
const COL2_X: f32 = COL1_X + COL1_W + COL_GAP;
const RIGHT_EDGE: f32 = FRAME_W - PAD;
/// The gap between a row's key column and its text, in canvas units across.
const KEY_GAP: f32 = 16.0;

// The type: the theme font nearest each canvas size times 0.9, the canvas's 1200 lines on a
// 1080-line screen. The canvas set everything in one face, so no weight is carried over; the
// headings are semibold because the theme's only 27 is.

/// Section headings, canvas 30 (27 at 1080p).
const HEADING_FONT: Font = Font::BoardHeading;
/// The unique section's opening paragraph, canvas 16 (14.4), and a gear row's name, the same.
const LEAD_FONT: Font = Font::BoardBody;
/// The infusion and parry sections' opening paragraphs and a gear row's text, canvas 15 (13.5).
const NOTE_FONT: Font = Font::Body;

// The vertical rhythm, in canvas units as the canvas spaced it: the type is the canvas's size
// again, so its gaps are too.

/// Below a section's heading, and below its opening paragraph.
const HEADING_GAP: f32 = 16.0;
const INTRO_GAP: f32 = 16.0;
/// Between a column's last section and its next heading: 40 in the first column, 36 under the
/// second's speed rows.
const SECTION_GAP: f32 = 40.0;
const SPEED_SECTION_GAP: f32 = 36.0;
/// Between a gear row's name and its text, and either side of the rule between gear rows.
const GEAR_NAME_GAP: f32 = 2.0;
const GEAR_ROW_GAP: f32 = 10.0;

/// Images the board draws, each `None` until the host has uploaded it.
pub struct BoardArt {
    /// Each gear row's icon, in the board's gear order.
    pub gear: Vec<Option<TextureId>>,
}

/// The gap between a gear row's icon and its text, in canvas units across. The icon itself is a
/// square as tall as the row's name and one line of its text (`Pen::gear`).
const GEAR_ICON_GAP: f32 = 12.0;

/// How a list of keyed rows is set: the key column's width in canvas units, the key's and the
/// text's fonts, and the gap either side of the rule between rows in canvas units.
#[derive(Clone, Copy)]
struct RowStyle {
    key_w: f32,
    key_font: Font,
    text_font: Font,
    gap: f32,
}

/// Keys at canvas 22 (19.8 at 1080p), text at 17 (15.3).
const UNIQUE_ROWS: RowStyle = RowStyle {
    key_w: 104.0,
    key_font: Font::BoardKey,
    text_font: Font::BoardBody,
    gap: 14.0,
};
/// A step smaller than the unique rows: keys at canvas 18 (16.2), text at 15 (13.5).
const INFUSION_ROWS: RowStyle = RowStyle {
    key_w: 104.0,
    key_font: Font::BoardBodyLarge,
    text_font: Font::Body,
    gap: 10.0,
};
const SPEED_ROWS: RowStyle = RowStyle {
    key_w: 88.0,
    ..UNIQUE_ROWS
};

/// Draws in canvas units, stretched to the whole screen: x by `kx`, y by `ky`. Type is the
/// theme's, at the panel's scale.
struct Pen<'a> {
    ui: &'a Ui,
    kx: f32,
    ky: f32,
    /// Lays out without drawing: every call returns the y it would, and nothing reaches the
    /// screen. How a section learns its height before deciding whether it scrolls.
    measure_only: bool,
}

impl Pen<'_> {
    /// A line of `font`, in canvas units down the screen.
    fn line(&self, font: Font) -> f32 {
        theme::line_height(self.ui, font) / self.ky
    }

    /// Draws `s` at canvas `(x, y)` in `font` and `colour`, wrapping at canvas `right`; returns the
    /// canvas y below it.
    fn text(&self, x: f32, y: f32, right: f32, font: Font, colour: [f32; 4], s: &str) -> f32 {
        let ui = self.ui;
        if self.measure_only {
            // The drawn text sizes its item by `CalcTextSize` at this font, scale and wrap width.
            let wrap_w = ((right - x) * self.kx).max(1.0);
            let height = theme::with_font(ui, font, || {
                ui.calc_text_size_with_opts(s, false, wrap_w)[1]
            });
            return y + height / self.ky;
        }
        ui.set_cursor_pos([x * self.kx, y * self.ky]);
        let _wrap = ui.push_text_wrap_pos_with_pos(right * self.kx);
        theme::text_wrapped(ui, font, colour, s);
        (ui.item_rect_max()[1] - ui.window_pos()[1]) / self.ky
    }

    /// A section's heading and the gap under it.
    fn heading(&self, x: f32, y: f32, right: f32, words: &str) -> f32 {
        self.text(x, y, right, HEADING_FONT, theme::ACCENT, words) + HEADING_GAP
    }

    /// A section's opening paragraph in `font` and the gap under it.
    fn intro(&self, x: f32, y: f32, right: f32, font: Font, words: &str) -> f32 {
        self.text(x, y, right, font, theme::TEXT_SECONDARY, words) + INTRO_GAP
    }

    /// The fainter rule between two rows, across canvas `x` to `right` at canvas `y`.
    fn rule(&self, x: f32, y: f32, right: f32) {
        if self.measure_only {
            return;
        }
        let origin = self.ui.window_pos();
        theme::painter(self.ui).hline(
            origin[0] + x * self.kx,
            origin[0] + right * self.kx,
            origin[1] + y * self.ky,
            theme::ROW_DIVIDER,
        );
    }

    /// Keyed rows, ruled between: the key in its own column, the text beside it.
    fn rows(&self, x: f32, mut y: f32, right: f32, style: RowStyle, rows: &[Line]) -> f32 {
        // The text's first line centred on the key's, which is set larger.
        let text_drop = (self.line(style.key_font) - self.line(style.text_font)) * 0.5;
        let gap = style.gap;
        for (i, row) in rows.iter().enumerate() {
            let key_right = x + style.key_w;
            let key_bottom = self.text(
                x,
                y,
                key_right,
                style.key_font,
                theme::SELECTED_NAME,
                row.key,
            );
            let text_x = key_right + KEY_GAP;
            let text_bottom = self.text(
                text_x,
                y + text_drop,
                right,
                style.text_font,
                theme::TEXT,
                row.text,
            );
            y = key_bottom.max(text_bottom);
            if i + 1 < rows.len() {
                y += gap;
                self.rule(x, y, right);
                y += gap;
            }
        }
        y
    }

    fn gear(
        &self,
        x: f32,
        mut y: f32,
        right: f32,
        gear: &[Gear],
        icons: &[Option<TextureId>],
    ) -> f32 {
        // The icon is as tall as the name and one line of the text under it, in canvas units, and
        // as wide on the screen as it is tall, so it stays square however the canvas stretches.
        let icon_h = self.line(LEAD_FONT) + GEAR_NAME_GAP + self.line(NOTE_FONT);
        let side = icon_h * self.ky;
        let text_x = x + side / self.kx + GEAR_ICON_GAP;
        for (i, g) in gear.iter().enumerate() {
            let top = y;
            if let (false, Some(Some(texture))) = (self.measure_only, icons.get(i)) {
                let origin = self.ui.window_pos();
                let p0 = [origin[0] + x * self.kx, origin[1] + top * self.ky];
                self.ui
                    .get_window_draw_list()
                    .add_image(*texture, p0, [p0[0] + side, p0[1] + side])
                    .build();
            }
            y = self.text(text_x, y, right, LEAD_FONT, theme::SELECTED_NAME, g.name)
                + GEAR_NAME_GAP;
            y = self.text(text_x, y, right, NOTE_FONT, theme::TEXT_SECONDARY, g.text);
            y = y.max(top + icon_h);
            if i + 1 < gear.len() {
                y += GEAR_ROW_GAP;
                self.rule(x, y, right);
                y += GEAR_ROW_GAP;
            }
        }
        y
    }
}

/// The board, over the whole screen, in the frame's theme ([`theme::enter`]).
pub fn draw(ui: &Ui, board: &Board, art: &BoardArt) {
    let size = ui.io().display_size;
    let (kx, ky) = (size[0] / FRAME_W, size[1] / FRAME_H);
    let origin = [0.0, 0.0];

    // The window draws no background of its own: the ground below covers only the board's part.
    let flags = WindowFlags::NO_DECORATION
        | WindowFlags::NO_INPUTS
        | WindowFlags::NO_BACKGROUND
        | WindowFlags::NO_SAVED_SETTINGS
        | WindowFlags::NO_NAV
        | WindowFlags::NO_FOCUS_ON_APPEARING
        | WindowFlags::NO_BRING_TO_FRONT_ON_FOCUS;
    let window_style = theme::window_style(ui);
    let begun = ui
        .window("##er-r3-view-board")
        .position(origin, Condition::Always)
        .size(size, Condition::Always)
        .flags(flags)
        .begin();
    drop(window_style);
    let Some(_window) = begun else {
        return;
    };
    theme::apply_font_scale(ui);
    let _body = theme::font(ui, Font::Body);
    let _style = theme::panel_style(ui);
    {
        // Ground only right of the game's item list, which stays uncovered.
        let divider_x = origin[0] + GAME_PANEL_W * kx;
        let paint = theme::painter(ui);
        paint.fill(
            [divider_x, origin[1]],
            [origin[0] + size[0], origin[1] + size[1]],
            theme::PANEL_BG,
            0.0,
        );
        paint.vline(divider_x, origin[1], origin[1] + size[1], theme::RULE);
    }

    let pen = Pen {
        ui,
        kx,
        ky,
        measure_only: false,
    };
    let col1_right = COL1_X + COL1_W;
    let y = pen.heading(COL1_X, PAD, col1_right, "What makes it unique");
    let y = pen.intro(COL1_X, y, col1_right, LEAD_FONT, board.unique_intro);
    let y = pen.rows(COL1_X, y, col1_right, UNIQUE_ROWS, board.unique) + SECTION_GAP;
    let y = pen.heading(COL1_X, y, col1_right, board.infusions_heading);
    let y = pen.intro(COL1_X, y, col1_right, NOTE_FONT, board.infusions_intro);
    let y = pen.rows(COL1_X, y, col1_right, INFUSION_ROWS, board.infusions) + SECTION_GAP;
    if !board.parry_intro.is_empty() {
        let y = pen.heading(COL1_X, y, col1_right, "Parrying");
        let y = pen.intro(COL1_X, y, col1_right, NOTE_FONT, board.parry_intro);
        pen.rows(COL1_X, y, col1_right, SPEED_ROWS, board.parry);
    }

    let y = pen.heading(COL2_X, PAD, RIGHT_EDGE, "Speed and cost");
    let y = pen.rows(COL2_X, y, RIGHT_EDGE, SPEED_ROWS, board.speed) + SPEED_SECTION_GAP;
    let y = pen.heading(COL2_X, y, RIGHT_EDGE, "Gear with synergy");
    // The screen's bottom in canvas units. The canvas is stretched to the display's height, so
    // this is `FRAME_H` at every resolution; type follows the panel's scale, the display's height
    // over 1080, so how much gear fits varies only with the aspect ratio, through the wrap width.
    let viewport_bottom = size[1] / ky - PAD;
    gear_section(&pen, board, &art.gear, y, viewport_bottom);
}

/// The gear rows from canvas `top`, as they are when they end above `bottom`. Taller than that,
/// they scroll inside `[top, bottom]` on [`crate::scroll::scroll_offset`]'s cycle, cut at its
/// edges, with a scroll bar in the right margin.
fn gear_section(pen: &Pen, board: &Board, icons: &[Option<TextureId>], top: f32, bottom: f32) {
    let measured = Pen {
        measure_only: true,
        ..*pen
    };
    let content_h = measured.gear(COL2_X, top, RIGHT_EDGE, board.gear, icons) - top;
    let viewport_h = bottom - top;
    if content_h <= viewport_h || viewport_h <= 0.0 {
        pen.gear(COL2_X, top, RIGHT_EDGE, board.gear, icons);
        return;
    }
    let t = scroll_clock(board);
    let offset = crate::scroll::scroll_offset(content_h, viewport_h, t);
    // Whole screen pixels: imgui floors text positions, and an offset between pixels would set
    // the rules and icons a pixel apart from their text on alternate frames.
    let offset = (offset * pen.ky).round() / pen.ky;

    let ui = pen.ui;
    let origin = ui.window_pos();
    let clip_min = [origin[0] + COL2_X * pen.kx, origin[1] + top * pen.ky];
    let clip_max = [origin[0] + FRAME_W * pen.kx, origin[1] + bottom * pen.ky];
    // SAFETY: inside the board window's frame; `igPushClipRect` narrows the window's clip rect
    // and its draw list's together, so items and the rules and icons drawn beside them are cut
    // alike, and it is popped below before anything else draws.
    unsafe {
        sys::igPushClipRect(
            sys::ImVec2::new(clip_min[0], clip_min[1]),
            sys::ImVec2::new(clip_max[0], clip_max[1]),
            true,
        );
    }
    pen.gear(COL2_X, top - offset, RIGHT_EDGE, board.gear, icons);
    // SAFETY: pops exactly the push above.
    unsafe { sys::igPopClipRect() };

    let travel = content_h - viewport_h;
    let thumb_h = (viewport_h * viewport_h / content_h).max(SCROLL_THUMB_MIN);
    let thumb_top = top + (viewport_h - thumb_h) * offset / travel;
    let bar_x0 = origin[0] + SCROLL_BAR_X * pen.kx;
    let bar_x1 = bar_x0 + theme::px(SCROLL_BAR_W);
    let paint = theme::painter(ui);
    paint.fill(
        [bar_x0, clip_min[1]],
        [bar_x1, clip_max[1]],
        theme::RULE,
        0.0,
    );
    paint.fill(
        [bar_x0, origin[1] + thumb_top * pen.ky],
        [bar_x1, origin[1] + (thumb_top + thumb_h) * pen.ky],
        theme::TEXT_QUIET,
        0.0,
    );
}

/// The scroll bar: a track in the right margin at canvas `SCROLL_BAR_X`, clear of the wrapped
/// text, `SCROLL_BAR_W` 1080p pixels wide at the panel's scale, so it is as thick as the type is
/// large, and a thumb no shorter than `SCROLL_THUMB_MIN` canvas units.
const SCROLL_BAR_X: f32 = RIGHT_EDGE + 20.0;
const SCROLL_BAR_W: f32 = 4.0;
const SCROLL_THUMB_MIN: f32 = 32.0;

/// The scroll clock starts again, at the top's pause, when the board comes back after being away
/// longer than this.
const SCROLL_RESTART_GAP: Duration = Duration::from_millis(500);

struct ScrollClock {
    board: usize,
    start: Instant,
    last: Instant,
}

static SCROLL_CLOCK: Mutex<Option<ScrollClock>> = Mutex::new(None);

/// Seconds into the scroll cycle for `board`, from the frame it came up: another weapon's board,
/// or the same one back after `SCROLL_RESTART_GAP`, starts at zero.
fn scroll_clock(board: &Board) -> f32 {
    let now = Instant::now();
    let key = board as *const Board as usize;
    let mut clock = SCROLL_CLOCK.lock().unwrap_or_else(|e| e.into_inner());
    let running = clock
        .as_ref()
        .is_some_and(|c| c.board == key && now.duration_since(c.last) <= SCROLL_RESTART_GAP);
    if !running {
        *clock = None;
    }
    let clock = clock.get_or_insert(ScrollClock {
        board: key,
        start: now,
        last: now,
    });
    clock.last = now;
    now.duration_since(clock.start).as_secs_f32()
}
