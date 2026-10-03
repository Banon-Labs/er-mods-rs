//! The weapon board drawn in view 3: the canvas "Weapon: what makes it good" laid out in imgui.
//!
//! Positions and sizes are the canvas's own, in its 1440 x 1200 frame, stretched edge to edge:
//! across by the screen's width, down and in type size by its height. The host's atlas has one font, so sizes come from the window font scale over the
//! host's base size, and the canvas's serif and mono faces are drawn in that one face.
//!
//! Every number on a board comes from the mechanics scripts named beside it.

use er_build_watermark_core::overlay_host::with_font;
use hudhook::imgui::sys::ImFont;
use hudhook::imgui::{Condition, StyleColor, StyleVar, TextureId, Ui, WindowFlags};

pub struct Line {
    pub key: &'static str,
    pub text: &'static str,
}

pub struct Gear {
    pub name: &'static str,
    pub text: &'static str,
}

pub struct Board {
    /// The weapon's `EquipParamWeapon` `iconId`, the `%05d` of its `MENU_ItemIcon_` symbol.
    pub icon_id: u32,
    pub class: &'static str,
    pub name: &'static str,
    pub rule: &'static str,
    pub unique_intro: &'static str,
    pub unique: &'static [Line],
    pub infusions_intro: &'static str,
    pub infusions: &'static [Line],
    pub speed: &'static [Line],
    pub gear: &'static [Gear],
}

/// Misericorde (1030000).
///
/// - "What makes it unique": `er-mechanics-weapon-twins.py Misericorde` -- no twin; nine infusable
///   daggers are comparable, and each row is an advantage it holds over the siblings it names.
/// - "Top Infusions": `er-mechanics-infusions.py Misericorde`, its `top physical`, `top
///   elemental` and `top status` rows (each infusion classed by what it adds over Standard).
/// - "Speed and cost": `er-mechanics-weapon-card.py Misericorde`.
/// - "Gear with synergy": `er-mechanics-gear-synergy.py Misericorde --top 6`: the dagger pair,
///   its critical gear (shown because its critical is above the median), the top six rows after
///   the setup discount, and Spear Talisman because its hits pierce.
pub const MISERICORDE: Board = Board {
    // EquipParamWeapon 1030000's iconId; its icon sits in atlas SB_Icon_03.
    icon_id: 10003,
    class: "DAGGER",
    name: "Misericorde",
    rule: "Can be infused",
    unique_intro: "Against the nine other daggers that can be infused; none is a twin, and \
                   Main-gauche shares the most of its moveset, 32 of 36 moves.",
    unique: &[
        Line {
            key: "155 MV",
            text: "Charged R2s, against 150 on seven of the nine other daggers that can be \
                   infused.",
        },
        Line {
            key: "R2 f8",
            text: "First R2 hits on frame 8, against 9 on six of the nine other daggers that can \
                   be infused.",
        },
        Line {
            key: "DEX 12",
            text: "To wield it, against 13 to 15 on Main-gauche, Parrying Dagger, Wakizashi and \
                   Fire Knight's Shortsword.",
        },
    ],
    infusions_intro: "Best RL 150 build: Keen from a Heavy Knight start, 547 AR (311th highest \
                      of 416) and 249 damage per hit (328th highest of 416), against every \
                      weapon's best RL 150 build with its best infusion and class.",
    infusions: &[
        Line {
            key: "Physical",
            text: "Heavy. Scaling STR 106.4. At RL 150 from a Heavy Knight start: STR 85 / DEX \
                   12, 543 AR with Drawstring Dragonbolt Grease. The best pick at RL 60-170; past \
                   RL 170 another infusion does more.",
        },
        Line {
            key: "Elemental",
            text: "Lightning. Scaling DEX 60.9, STR 7.2. At RL 150 from a Heavy Knight start: STR \
                   15 / DEX 82, 482 AR. Never the best pick between RL 60 and 200.",
        },
        Line {
            key: "Status",
            text: STATUS_ROW,
        },
    ],
    speed: &[
        Line {
            key: "10 f",
            text: "R1 hits on frame 10 (tied 1st fastest of 451) and reaches 3.0 m (tied 268th \
                   longest of 419).",
        },
        Line {
            key: "9 sp",
            text: "Stamina per R1 one-handed (tied 47th cheapest of 451), 12 two-handed (tied \
                   94th cheapest of 451).",
        },
    ],
    gear: &[
        Gear {
            name: "A second dagger",
            text: "Powerstance with another Misericorde or any of 16 other daggers adds a \
                   backstep L1 that hits on frame 11 (tied 1st fastest of 320).",
        },
        Gear {
            name: "Dagger Talisman",
            text: "+17% critical damage. Misericorde's critical is x1.40, the highest of 327 \
                   weapons that can crit; the median is x1.00.",
        },
        Gear {
            name: "Twinblade Talisman",
            text: "+45% on the final hit of a chain.",
        },
        Gear {
            name: "Two-Handed Sword Talisman",
            text: "+10% on two-handed attacks.",
        },
        Gear {
            name: "Axe Talisman",
            text: "+10% on charged heavy attacks.",
        },
        Gear {
            name: "Lacerating Crossed-Tree",
            text: "+8% on dash attacks.",
        },
        Gear {
            name: "Leda's Armor",
            text: "+5% on dash attacks. +5% on backstep / rolling attacks, worth half for the \
                   setup they need. Costs 0.9% more damage taken.",
        },
        Gear {
            name: "Millicent's Prosthesis",
            text: "+3% from its DEX, +4% on successive hits.",
        },
        Gear {
            name: "Spear Talisman",
            text: "+15% counter-hit damage; 39% of its hits pierce.",
        },
    ],
};

const STATUS_ROW: &str = "Poison. Scaling DEX 89.3, ARC 43.5, STR 26.6. At RL 150 from a Heavy \
                          Knight start: STR 15 / DEX 73 / ARC 18, 363 AR. Never the best pick \
                          between RL 60 and 200. Poison buildup 98.";

const FRAME_W: f32 = 1440.0;
pub const FRAME_H: f32 = 1200.0;
const LEFT_W: f32 = 400.0;
const PAD: f32 = 56.0;
const COL_GAP: f32 = 48.0;
const COL1_W: f32 = (FRAME_W - LEFT_W - 2.0 * PAD - COL_GAP) * 1.25 / 2.25;
const COL1_X: f32 = LEFT_W + PAD;
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
const LABEL: [f32; 4] = rgb(0xa89f8a);
const BRIGHT: [f32; 4] = rgb(0xf2e8cf);
const GOLD: [f32; 4] = rgb(0xe3c27a);
const ICON_FILL: [f32; 4] = rgb(0x13110d);
const ICON_EDGE: [f32; 4] = rgb(0x3a3426);

/// The canvas's "[weapon render]" slot in the left column: its width spans the name block's
/// column, `PAD` to `LEFT_W - 40`, and it starts no higher than the design's y of 180.
const ICON_BOX_W: f32 = LEFT_W - 40.0 - PAD;
const ICON_BOX_H: f32 = 360.0;
const ICON_BOX_TOP: f32 = 180.0;
const ICON_BOX_GAP: f32 = 16.0;

/// Images the board draws, each `None` until the host has uploaded it.
pub struct BoardArt {
    /// The weapon's icon: the host's texture id and the image's pixel size.
    pub icon: Option<(TextureId, [f32; 2])>,
}

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
}

impl Pen<'_> {
    /// Draws `s` at canvas `(x, y)` in `px`, wrapping at canvas `right`; returns the canvas y
    /// below it. In the game's font when the host has built it at `px`, else the host's default
    /// face scaled.
    fn text(&self, x: f32, y: f32, right: f32, px: f32, color: [f32; 4], s: &str) -> f32 {
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
            ui.set_cursor_pos([x * self.kx, y * self.ky]);
            let _wrap = ui.push_text_wrap_pos_with_pos(right * self.kx);
            let _color = ui.push_style_color(StyleColor::Text, color);
            ui.text(s);
            (ui.item_rect_max()[1] - ui.window_pos()[1]) / self.ky
        })
    }

    fn rule(&self, x: f32, y: f32, right: f32) {
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

    fn gear(&self, x: f32, mut y: f32, right: f32, gear: &[Gear]) -> f32 {
        for (i, g) in gear.iter().enumerate() {
            y = self.text(x, y, right, 16.0, BRIGHT, g.name) + 2.0;
            y = self.text(x, y, right, 15.0, MUTED, g.text);
            if i + 1 < gear.len() {
                y += 10.0;
                self.rule(x, y, right);
                y += 10.0;
            }
        }
        y
    }
}

/// The icon slot at canvas y `top`: the bordered box, and the image across its width at the
/// image's own aspect (fitted to the height instead if that would overflow), centred.
fn draw_icon(ui: &Ui, kx: f32, ky: f32, top: f32, texture: TextureId, size: [f32; 2]) {
    if size[0] <= 0.0 || size[1] <= 0.0 {
        return;
    }
    let origin = ui.window_pos();
    let p0 = [origin[0] + PAD * kx, origin[1] + top * ky];
    let (box_w, box_h) = (ICON_BOX_W * kx, ICON_BOX_H * ky);
    let p1 = [p0[0] + box_w, p0[1] + box_h];
    let draw_list = ui.get_window_draw_list();
    draw_list.add_rect(p0, p1, ICON_FILL).filled(true).build();
    draw_list.add_rect(p0, p1, ICON_EDGE).thickness(1.0).build();
    let mut w = box_w;
    let mut h = w * size[1] / size[0];
    if h > box_h {
        h = box_h;
        w = h * size[0] / size[1];
    }
    let i0 = [p0[0] + (box_w - w) / 2.0, p0[1] + (box_h - h) / 2.0];
    draw_list
        .add_image(texture, i0, [i0[0] + w, i0[1] + h])
        .build();
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
        let draw_list = ui.get_window_draw_list();
        draw_list
            .add_rect(origin, [origin[0] + size[0], origin[1] + size[1]], GROUND)
            .filled(true)
            .build();
        let divider_x = origin[0] + LEFT_W * kx;
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
    };
    let left_right = LEFT_W - 40.0;
    let y = pen.text(PAD, PAD, left_right, 13.0, LABEL, board.class) + 6.0;
    let y = pen.text(PAD, y, left_right, 52.0, BRIGHT, board.name) + 6.0;
    let y = pen.text(PAD, y, left_right, 15.0, MUTED, board.rule);
    if let Some((texture, size)) = art.icon {
        draw_icon(
            ui,
            kx,
            ky,
            ICON_BOX_TOP.max(y + ICON_BOX_GAP),
            texture,
            size,
        );
    }

    let col1_right = COL1_X + COL1_W;
    let y = pen.text(COL1_X, PAD, col1_right, 30.0, GOLD, "What makes it unique") + 16.0;
    let y = pen.text(COL1_X, y, col1_right, 16.0, MUTED, board.unique_intro) + 16.0;
    let y = pen.rows(COL1_X, y, col1_right, UNIQUE_ROWS, board.unique) + 40.0;
    let y = pen.text(COL1_X, y, col1_right, 30.0, GOLD, "Top Infusions") + 16.0;
    let y = pen.text(COL1_X, y, col1_right, 15.0, MUTED, board.infusions_intro) + 16.0;
    pen.rows(COL1_X, y, col1_right, INFUSION_ROWS, board.infusions);

    let y = pen.text(COL2_X, PAD, RIGHT_EDGE, 30.0, GOLD, "Speed and cost") + 16.0;
    let y = pen.rows(COL2_X, y, RIGHT_EDGE, SPEED_ROWS, board.speed) + 36.0;
    let y = pen.text(COL2_X, y, RIGHT_EDGE, 30.0, GOLD, "Gear with synergy") + 16.0;
    pen.gear(COL2_X, y, RIGHT_EDGE, board.gear);
}
