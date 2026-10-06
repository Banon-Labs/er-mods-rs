//! The weapon board drawn in view 3: the canvas "Weapon: what makes it good" laid out in imgui.
//!
//! Positions and sizes are the canvas's own, in its 1440 x 1200 frame, stretched edge to edge:
//! across by the screen's width, down and in type size by its height. The host's atlas has one font, so sizes come from the window font scale over the
//! host's base size, and the canvas's serif and mono faces are drawn in that one face.
//!
//! Every board is generated from the mechanics scripts into `weapon_boards.rs` by
//! `scripts/gen-r3-weapon-boards.py`.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use er_build_watermark_core::overlay_host::with_font;
use hudhook::imgui::sys::{self, ImFont};
use hudhook::imgui::{Condition, StyleColor, StyleVar, TextureId, Ui, WindowFlags};

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
pub const FRAME_H: f32 = 1200.0;
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

const fn rgb(hex: u32) -> [f32; 4] {
    [
        ((hex >> 16) & 0xff) as f32 / 255.0,
        ((hex >> 8) & 0xff) as f32 / 255.0,
        (hex & 0xff) as f32 / 255.0,
        1.0,
    ]
}

const GROUND: [f32; 4] = {
    let mut c = rgb(0x0d0c0a);
    c[3] = 0.94;
    c
};
const RULE: [f32; 4] = rgb(0x2c281f);
const BODY: [f32; 4] = rgb(0xe9e3d3);
const MUTED: [f32; 4] = rgb(0xc9c0aa);
const BRIGHT: [f32; 4] = rgb(0xf2e8cf);
const GOLD: [f32; 4] = rgb(0xe3c27a);

/// Images the board draws, each `None` until the host has uploaded it.
pub struct BoardArt {
    /// Each gear row's icon, in the board's gear order.
    pub gear: Vec<Option<TextureId>>,
}

/// A gear row's icon: a square of this canvas height left of the row, its text indented past it.
const GEAR_ICON: f32 = 44.0;
const GEAR_ICON_GAP: f32 = 12.0;

/// How a list of keyed rows is set: the key column's width, both sizes and the gap either side
/// of the rule between rows.
#[derive(Clone, Copy)]
struct RowStyle {
    key_w: f32,
    key_px: f32,
    text_px: f32,
    gap: f32,
}

const UNIQUE_ROWS: RowStyle = RowStyle {
    key_w: 104.0,
    key_px: 22.0,
    text_px: 17.0,
    gap: 14.0,
};
const INFUSION_ROWS: RowStyle = RowStyle {
    key_w: 104.0,
    key_px: 18.0,
    text_px: 15.0,
    gap: 10.0,
};
const SPEED_ROWS: RowStyle = RowStyle {
    key_w: 88.0,
    ..UNIQUE_ROWS
};

/// Every canvas size the board sets type in; the host builds one face of the game's font per size.
pub const SIZES: [f32; 8] = [13.0, 15.0, 16.0, 17.0, 18.0, 22.0, 30.0, 52.0];

/// Agmena's ascent minus descent over its em, 1467 / 1024: imgui sizes a face by that height and
/// the canvas by its em, so a canvas size becomes `px * ky * LINE_PER_EM` imgui pixels.
pub const LINE_PER_EM: f32 = 1467.0 / 1024.0;

/// The game's font at each of [`SIZES`], built for screen scale `ky`; `None` until the host has
/// built that face.
pub struct Fonts {
    pub faces: [Option<*mut ImFont>; SIZES.len()],
    pub ky: f32,
}

/// Draws in canvas units, stretched to the whole screen: x by `kx`, y and type sizes by `ky`.
struct Pen<'a> {
    ui: &'a Ui,
    kx: f32,
    ky: f32,
    base_px: f32,
    fonts: Option<&'a Fonts>,
    /// Lays out without drawing: every call returns the y it would, and nothing reaches the
    /// screen. How a section learns its height before deciding whether it scrolls.
    measure_only: bool,
}

impl Pen<'_> {
    /// Draws `s` at canvas `(x, y)` in `px`, wrapping at canvas `right`; returns the canvas y
    /// below it. In the game's font when the host has built it at `px`, else the host's default
    /// face scaled.
    fn text(&self, x: f32, y: f32, right: f32, px: f32, color: [f32; 4], s: &str) -> f32 {
        let ui = self.ui;
        self.in_face(px, || {
            if self.measure_only {
                // `ui.text` sizes its item by `CalcTextSize` at this face, scale and wrap width.
                let wrap_w = ((right - x) * self.kx).max(1.0);
                return y + ui.calc_text_size_with_opts(s, false, wrap_w)[1] / self.ky;
            }
            ui.set_cursor_pos([x * self.kx, y * self.ky]);
            let _wrap = ui.push_text_wrap_pos_with_pos(right * self.kx);
            let _color = ui.push_style_color(StyleColor::Text, color);
            ui.text(s);
            (ui.item_rect_max()[1] - ui.window_pos()[1]) / self.ky
        })
    }

    /// Runs `f` with the face and window font scale that set type at canvas size `px`.
    fn in_face<R>(&self, px: f32, f: impl FnOnce() -> R) -> R {
        let ui = self.ui;
        let face = self.fonts.and_then(|fonts| {
            let i = SIZES.iter().position(|&size| size == px)?;
            fonts.faces[i].map(|font| (font, fonts.ky))
        });
        with_font(ui, face.map(|(font, _)| font), || {
            match face {
                Some((_, built_ky)) => ui.set_window_font_scale(self.ky / built_ky),
                None => ui.set_window_font_scale(px * self.ky / self.base_px),
            }
            f()
        })
    }

    fn rule(&self, x: f32, y: f32, right: f32) {
        if self.measure_only {
            return;
        }
        let origin = self.ui.window_pos();
        self.ui
            .get_window_draw_list()
            .add_line(
                [origin[0] + x * self.kx, origin[1] + y * self.ky],
                [origin[0] + right * self.kx, origin[1] + y * self.ky],
                RULE,
            )
            .build();
    }

    /// Keyed rows, ruled between: the key in its own column, the text beside it.
    fn rows(&self, x: f32, mut y: f32, right: f32, style: RowStyle, rows: &[Line]) -> f32 {
        for (i, row) in rows.iter().enumerate() {
            let key_bottom = self.text(x, y, x + style.key_w, style.key_px, BRIGHT, row.key);
            let text_x = x + style.key_w + 16.0;
            let text_y = y + (style.key_px - style.text_px) / 2.0;
            let text_bottom = self.text(text_x, text_y, right, style.text_px, BODY, row.text);
            y = key_bottom.max(text_bottom);
            if i + 1 < rows.len() {
                y += style.gap;
                self.rule(x, y, right);
                y += style.gap;
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
        let text_x = x + GEAR_ICON + GEAR_ICON_GAP;
        for (i, g) in gear.iter().enumerate() {
            let top = y;
            if let (false, Some(Some(texture))) = (self.measure_only, icons.get(i)) {
                let origin = self.ui.window_pos();
                let p0 = [origin[0] + x * self.kx, origin[1] + top * self.ky];
                // The canvas stretches x and y apart, so the side is set by y alone to stay square.
                let side = GEAR_ICON * self.ky;
                self.ui
                    .get_window_draw_list()
                    .add_image(*texture, p0, [p0[0] + side, p0[1] + side])
                    .build();
            }
            y = self.text(text_x, y, right, 16.0, BRIGHT, g.name) + 2.0;
            y = self.text(text_x, y, right, 15.0, MUTED, g.text);
            y = y.max(top + GEAR_ICON);
            if i + 1 < gear.len() {
                y += 10.0;
                self.rule(x, y, right);
                y += 10.0;
            }
        }
        y
    }
}

pub fn draw(ui: &Ui, board: &Board, fonts: Option<&Fonts>, art: &BoardArt) {
    let size = ui.io().display_size;
    let (kx, ky) = (size[0] / FRAME_W, size[1] / FRAME_H);
    let origin = [0.0, 0.0];

    let _padding = ui.push_style_var(StyleVar::WindowPadding([0.0, 0.0]));
    let _border = ui.push_style_var(StyleVar::WindowBorderSize(0.0));
    let flags = WindowFlags::NO_DECORATION
        | WindowFlags::NO_INPUTS
        | WindowFlags::NO_BACKGROUND
        | WindowFlags::NO_SAVED_SETTINGS
        | WindowFlags::NO_NAV
        | WindowFlags::NO_FOCUS_ON_APPEARING
        | WindowFlags::NO_BRING_TO_FRONT_ON_FOCUS;
    let Some(_window) = ui
        .window("##er-r3-view-board")
        .position(origin, Condition::Always)
        .size(size, Condition::Always)
        .flags(flags)
        .begin()
    else {
        return;
    };
    {
        // Ground only right of the game's item list, which stays uncovered.
        let divider_x = origin[0] + GAME_PANEL_W * kx;
        let draw_list = ui.get_window_draw_list();
        draw_list
            .add_rect(
                [divider_x, origin[1]],
                [origin[0] + size[0], origin[1] + size[1]],
                GROUND,
            )
            .filled(true)
            .build();
        draw_list
            .add_line(
                [divider_x, origin[1]],
                [divider_x, origin[1] + size[1]],
                RULE,
            )
            .build();
    }

    ui.set_window_font_scale(1.0);
    let pen = Pen {
        ui,
        kx,
        ky,
        base_px: ui.current_font_size(),
        fonts,
        measure_only: false,
    };
    let col1_right = COL1_X + COL1_W;
    let y = pen.text(COL1_X, PAD, col1_right, 30.0, GOLD, "What makes it unique") + 16.0;
    let y = pen.text(COL1_X, y, col1_right, 16.0, MUTED, board.unique_intro) + 16.0;
    let y = pen.rows(COL1_X, y, col1_right, UNIQUE_ROWS, board.unique) + 40.0;
    let y = pen.text(COL1_X, y, col1_right, 30.0, GOLD, board.infusions_heading) + 16.0;
    let y = pen.text(COL1_X, y, col1_right, 15.0, MUTED, board.infusions_intro) + 16.0;
    let y = pen.rows(COL1_X, y, col1_right, INFUSION_ROWS, board.infusions) + 40.0;
    if !board.parry_intro.is_empty() {
        let y = pen.text(COL1_X, y, col1_right, 30.0, GOLD, "Parrying") + 16.0;
        let y = pen.text(COL1_X, y, col1_right, 15.0, MUTED, board.parry_intro) + 16.0;
        pen.rows(COL1_X, y, col1_right, SPEED_ROWS, board.parry);
    }

    let y = pen.text(COL2_X, PAD, RIGHT_EDGE, 30.0, GOLD, "Speed and cost") + 16.0;
    let y = pen.rows(COL2_X, y, RIGHT_EDGE, SPEED_ROWS, board.speed) + 36.0;
    let y = pen.text(COL2_X, y, RIGHT_EDGE, 30.0, GOLD, "Gear with synergy") + 16.0;
    // The screen's bottom in canvas units. The canvas is stretched to the display's height, so
    // this is `FRAME_H` at every resolution; type is sized by `ky` too, so how much gear fits
    // varies only with the aspect ratio, through the wrap width.
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
    let bar_x1 = bar_x0 + SCROLL_BAR_W * pen.ky;
    let draw_list = ui.get_window_draw_list();
    draw_list
        .add_rect([bar_x0, clip_min[1]], [bar_x1, clip_max[1]], RULE)
        .filled(true)
        .build();
    draw_list
        .add_rect(
            [bar_x0, origin[1] + thumb_top * pen.ky],
            [bar_x1, origin[1] + (thumb_top + thumb_h) * pen.ky],
            MUTED,
        )
        .filled(true)
        .build();
}

/// The scroll bar: a track in the right margin, clear of the wrapped text, `SCROLL_BAR_W` canvas
/// units wide (set by `ky`, so it is as thick as the type is large), and a thumb no shorter than
/// `SCROLL_THUMB_MIN`.
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
