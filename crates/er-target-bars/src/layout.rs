//! Where the game's own HP bar for the locked-on target is, and where the copies go under it.
//!
//! Nothing here draws a second HP bar. The game already shows one for the target -- a floating
//! enemy tag over its head, or a boss bar along the bottom -- and the extra bars are copies of that
//! bar's art (see [`crate::art`]) hung directly under it, at its left edge and width, and gone
//! whenever the game hides it.
//!
//! # Where the numbers come from
//!
//! Positions are the game's own: `CSFeManImp+0x80` holds the `FrontEndViewValues` the HUD movie
//! reads, with `chrNameHudEnemy[8]` at `+0x11d0` and `chrNameHudBoss[3]` at `+0x1b10`, `0x128`
//! bytes each (1.16.2 named dump; on 1.17.1 `UpdateEnemyTags` 0x1407724f0 still addresses the
//! sibling `chrEnemyTagDisplays` at `+0x59f0`). Measured live 2026-10-04 with
//! `scripts/frida/target-bars-tag-probe.js`: the locked-on enemy's entry carried its handle,
//! `isVisible` 1, and `screenX/Y` as `i32` (959, 567 and onward, tracking the enemy) -- the same
//! values the `chrEnemyTagDisplays` float pair held, so they are HUD stage pixels.
//!
//! The geometry is the HUD movie's, `menu/01_000_fe.gfx` (1920x1080 stage, `EnemyTag0..7` and
//! `BossList` on the root timeline):
//!
//! * Enemy tag. The base is shape 286 (`x 0..143`, `y -7.95..7.55`) placed at `(-69.05, 0.2)`,
//!   filled with image 270 `MENU_FL_HP_Base` through the bitmap matrix `9.7577 x 7.0439` twips
//!   per texel, translated `(-1284.55, -7.95)`: about half a stage pixel per texel across, so
//!   the 143-pixel shape shows only the image's right end, texels `2633..2926` of 2926, at full
//!   height. Drawing the whole image into the tag's base would squeeze it tenfold. The fill is
//!   image 283 `MENU_FL_Red` placed at `(-70, -4.85)` at scale `0.3` (`Current` at `-68`, its
//!   child at `(-2, -4.85)` scale `0.6`, the image at `0.5`), clipped by mask 291: shape 290
//!   scaled `0.0915 x 0.1277` at `x 66` inside `Current`, i.e. `x -70..66`, `y -3..3`. So the
//!   visible fill is the image's `x 0..453.3`, `y 6.17..26.17` of its 2898 x 32.
//! * Boss bar `k`. Its `HP` sprite sits at `(-482.15, -35 - 55k)` inside `BossList` at
//!   `(947, 908)`, so its origin is stage `(464.85, 873 - 55k)`. The base is sprite 323, which
//!   places the whole of `MENU_FL_HP_Base` at scale `0.5`, itself placed at `(-6.35, -11.1)`
//!   scaled `0.6909 x 1`: `x -6.35..1004.3`, `y -11.1..10.9`. The fill is `MENU_FL_Red` at
//!   `(-2.7, -9)` scale `0.5`, clipped by mask 291 scaled `0.6726 x 0.3404` at `x 497`:
//!   `x -3..997`, `y -8..8`, i.e. the image's `x 0..1999.4`, `y 2..32`.

// Several items here are consumed only by the Windows build; the host build keeps them for tests.
#![cfg_attr(not(windows), allow(dead_code))]

use crate::model::{self, LockState};

/// The HUD movie's stage, in pixels.
pub const STAGE_WIDTH: f32 = 1920.0;
pub const STAGE_HEIGHT: f32 = 1080.0;

/// `BossList` origin plus `Item_0_0` and the `HP` sprite inside it, and the step between bosses.
pub const BOSS_ORIGIN: [f32; 2] = [947.0 - 482.15, 908.0 - 35.0];
pub const BOSS_ITEM_STEP: f32 = 55.0;

/// `MENU_FL_Red`'s size in its atlas, measured live 2026-10-04
/// (`scripts/frida/target-bars-hud-art-probe.js`: rect 0,113..2898,145 of a 4096 x 512 BC7 atlas).
pub const FILL_IMAGE_SIZE: [f32; 2] = [2898.0, 32.0];
/// `MENU_FL_HP_Base`'s size, from the same probe (rect 0,29..2926,73) and the movie's own
/// `DefineExternalImage2` for image 270.
pub const BASE_IMAGE_SIZE: [f32; 2] = [2926.0, 44.0];
/// Shape 286's bitmap matrix: horizontal scale in twips per texel, and translation in pixels.
pub const TAG_BASE_TWIPS_PER_TEXEL: f32 = 9.757_69;
pub const TAG_BASE_IMAGE_X: f32 = -1284.55;
/// The first texel column shape 286 shows, as a fraction of the base image's width.
pub const TAG_BASE_U0: f32 =
    -TAG_BASE_IMAGE_X / (BASE_IMAGE_SIZE[0] * TAG_BASE_TWIPS_PER_TEXEL / 20.0);

/// One game bar's art, relative to the bar's origin, in stage pixels: the base under it, the
/// visible fill, and which part of the fill image that visible fill shows.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BarArt {
    /// `[left, top, right, bottom]` of the `MENU_FL_HP_Base` base.
    pub base: [f32; 4],
    /// `[u0, v0, u1, v1]` of the base image that base shows.
    pub base_uv: [f32; 4],
    /// `[left, top, right, bottom]` of the full (100%) fill.
    pub fill: [f32; 4],
    /// `[u0, v0, u1, v1]` of the fill image shown by the full fill.
    pub fill_uv: [f32; 4],
}

/// The enemy tag's bar.
pub const TAG_ART: BarArt = BarArt {
    base: [-69.05, -7.75, 73.95, 7.75],
    base_uv: [TAG_BASE_U0, 0.0, 1.0, 1.0],
    fill: [-70.0, -3.0, 66.0, 3.0],
    fill_uv: [
        0.0,
        6.17 / FILL_IMAGE_SIZE[1],
        453.3 / FILL_IMAGE_SIZE[0],
        26.17 / FILL_IMAGE_SIZE[1],
    ],
};

/// A boss bar.
pub const BOSS_ART: BarArt = BarArt {
    base: [-6.35, -11.1, 1004.3, 10.9],
    base_uv: [0.0, 0.0, 1.0, 1.0],
    fill: [-3.0, -8.0, 997.0, 8.0],
    fill_uv: [
        0.0,
        2.0 / FILL_IMAGE_SIZE[1],
        1999.4 / FILL_IMAGE_SIZE[0],
        1.0,
    ],
};

/// Copies are the game bar's width and this fraction of its height, so a stack of them reads as
/// an addition to the game's bar rather than as more health bars.
pub const TAG_COPY_HEIGHT_SCALE: f32 = 0.75;
pub const BOSS_COPY_HEIGHT_SCALE: f32 = 0.5;
/// Gap between the game's bar and the first copy's fill, and between the fills of consecutive
/// copies, in stage pixels. Copies are stacked by fill height, not base height: the base art is
/// mostly margin above and below its frame, so stacking whole bases left about a bar's height of
/// empty space between strips (user report 2026-10-04). The bases overlap; render draws every
/// base before any fill so no fill is covered.
pub const COPY_GAP: f32 = 1.5;

/// One `ChrNameHudData` entry, as much of it as the anchor needs.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct HudEntry {
    pub visible: bool,
    pub not_on_screen: bool,
    pub handle: u64,
    pub x: i32,
    pub y: i32,
}

/// The game's HP bar for the target, in stage pixels.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Anchor {
    /// The floating tag; `(x, y)` is the tag's origin.
    EnemyTag { x: f32, y: f32 },
    /// Boss bar `slot` along the bottom.
    Boss { slot: usize },
}

impl Anchor {
    /// The game bar's art, origin and copy height scale.
    fn art(self) -> (BarArt, [f32; 2], f32) {
        match self {
            Anchor::EnemyTag { x, y } => (TAG_ART, [x, y], TAG_COPY_HEIGHT_SCALE),
            Anchor::Boss { slot } => (
                BOSS_ART,
                [
                    BOSS_ORIGIN[0],
                    BOSS_ORIGIN[1] - BOSS_ITEM_STEP * slot as f32,
                ],
                BOSS_COPY_HEIGHT_SCALE,
            ),
        }
    }
}

/// `CSFeManImp::hudState` value that hides the whole HUD.
pub const HUD_STATE_HIDE_ALL: u8 = 0;

/// Find the game's HP bar for `want` among the shown tags and boss bars, or `None` when the game
/// is not showing one -- in which case nothing is drawn.
///
/// A boss bar wins over a tag: the game does not normally show both, and when a boss is locked on
/// the bottom bar is the one the player reads.
pub fn pick_anchor(
    want: u64,
    hud_state: u8,
    enemy: &[HudEntry],
    boss: &[HudEntry],
) -> Option<Anchor> {
    if hud_state == HUD_STATE_HIDE_ALL || model::lock_state(want as u32) != LockState::Character {
        return None;
    }
    if let Some(slot) = boss
        .iter()
        .position(|entry| entry.visible && entry.handle == want)
    {
        return Some(Anchor::Boss { slot });
    }
    enemy
        .iter()
        .find(|entry| entry.visible && !entry.not_on_screen && entry.handle == want)
        .map(|entry| Anchor::EnemyTag {
            x: entry.x as f32,
            y: entry.y as f32,
        })
}

/// One copy of the game bar on the stage.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BarCopy {
    /// `[left, top, right, bottom]` of the base.
    pub base: [f32; 4],
    /// `[u0, v0, u1, v1]` of the base image.
    pub base_uv: [f32; 4],
    /// `[left, top, right, bottom]` of the full fill.
    pub fill: [f32; 4],
    /// `[u0, v0, u1, v1]` of the fill image for the full fill.
    pub fill_uv: [f32; 4],
}

impl BarCopy {
    /// The fill rectangle and its UVs at `fraction` full: the art is cropped, not squeezed, so a
    /// half bar shows the left half of the full bar's image exactly as the game's own does.
    pub fn fill_at(self, fraction: f32) -> ([f32; 4], [f32; 4]) {
        let fraction = model::fraction(fraction, 1.0);
        let [left, top, right, bottom] = self.fill;
        let [u0, v0, u1, v1] = self.fill_uv;
        (
            [left, top, left + (right - left) * fraction, bottom],
            [u0, v0, u0 + (u1 - u0) * fraction, v1],
        )
    }
}

/// The `index`th copy under the game's bar, on the stage.
pub fn copy_at(anchor: Anchor, index: usize) -> BarCopy {
    let (art, [x, y], height_scale) = anchor.art();
    let fill_height = (art.fill[3] - art.fill[1]) * height_scale;
    // The centre line of copy `index`. The first copy's base must not reach up over the game
    // bar's own fill, so its fill starts below the game's fill by the copy's top margin plus a
    // gap; after that, one fill height plus a gap per copy.
    let top_margin = (art.fill[1] - art.base[1]) * height_scale;
    let first_center = y + art.fill[3] + top_margin + COPY_GAP - art.fill[1] * height_scale;
    let center = first_center + index as f32 * (fill_height + COPY_GAP);
    let place = |rect: [f32; 4]| {
        [
            x + rect[0],
            center + rect[1] * height_scale,
            x + rect[2],
            center + rect[3] * height_scale,
        ]
    };
    BarCopy {
        base: place(art.base),
        base_uv: art.base_uv,
        fill: place(art.fill),
        fill_uv: art.fill_uv,
    }
}

/// How the 1920x1080 stage lands on the display: uniform scale, centred (letterboxed on a display
/// of another aspect).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct StageTransform {
    pub scale: f32,
    pub offset: [f32; 2],
}

impl StageTransform {
    pub fn for_display(display: [f32; 2]) -> Option<Self> {
        let [width, height] = display;
        if !(width.is_finite() && height.is_finite()) || width <= 0.0 || height <= 0.0 {
            return None;
        }
        let scale = (width / STAGE_WIDTH).min(height / STAGE_HEIGHT);
        Some(Self {
            scale,
            offset: [
                (width - STAGE_WIDTH * scale) / 2.0,
                (height - STAGE_HEIGHT * scale) / 2.0,
            ],
        })
    }

    /// A stage rectangle on the display.
    pub fn rect(self, rect: [f32; 4]) -> [f32; 4] {
        [
            self.offset[0] + rect[0] * self.scale,
            self.offset[1] + rect[1] * self.scale,
            self.offset[0] + rect[2] * self.scale,
            self.offset[1] + rect[3] * self.scale,
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LOCKED: u64 = 0x0a00_0000_1000_00ca;

    fn tag(handle: u64, x: i32, y: i32) -> HudEntry {
        HudEntry {
            visible: true,
            not_on_screen: false,
            handle,
            x,
            y,
        }
    }

    #[test]
    fn the_locked_enemy_tag_is_the_anchor() {
        let enemy = [tag(0x0a00_0000_1000_005c, 400, 300), tag(LOCKED, 959, 567)];
        assert_eq!(
            pick_anchor(LOCKED, 3, &enemy, &[]),
            Some(Anchor::EnemyTag { x: 959.0, y: 567.0 })
        );
    }

    #[test]
    fn nothing_is_drawn_when_the_game_hides_its_bar() {
        let mut hidden = tag(LOCKED, 959, 567);
        hidden.visible = false;
        assert_eq!(pick_anchor(LOCKED, 3, &[hidden], &[]), None);
        let mut off_screen = tag(LOCKED, 959, 567);
        off_screen.not_on_screen = true;
        assert_eq!(pick_anchor(LOCKED, 3, &[off_screen], &[]), None);
        assert_eq!(
            pick_anchor(LOCKED, HUD_STATE_HIDE_ALL, &[tag(LOCKED, 1, 1)], &[]),
            None
        );
        assert_eq!(pick_anchor(u64::MAX, 3, &[tag(u64::MAX, 1, 1)], &[]), None);
        assert_eq!(
            pick_anchor(LOCKED, 3, &[tag(0x0a00_0000_1000_005c, 1, 1)], &[]),
            None
        );
    }

    #[test]
    fn a_shown_boss_bar_wins_over_a_tag() {
        let boss = [HudEntry::default(), tag(LOCKED, 0, 0)];
        assert_eq!(
            pick_anchor(LOCKED, 3, &[tag(LOCKED, 959, 567)], &boss),
            Some(Anchor::Boss { slot: 1 })
        );
    }

    #[test]
    fn tag_copies_hang_under_the_game_bar_at_its_left_edge_and_width() {
        let anchor = Anchor::EnemyTag { x: 959.0, y: 567.0 };
        let first = copy_at(anchor, 0);
        assert_eq!(first.base[0], 959.0 - 69.05);
        assert_eq!(first.base[2], 959.0 + 73.95);
        assert_eq!(first.fill[0], 959.0 - 70.0);
        assert_eq!(first.fill[2] - first.fill[0], 136.0);
        // The first copy's base starts one gap below the game bar's fill, never over it.
        assert!((first.base[1] - (567.0 + 3.0 + COPY_GAP)).abs() < 1e-3);
        let second = copy_at(anchor, 1);
        assert_eq!(second.base[0], first.base[0]);
        // Fills stack one gap apart; bases may overlap.
        assert!((second.fill[1] - first.fill[3] - COPY_GAP).abs() < 1e-3);
        assert!((first.base[3] - first.base[1] - 15.5 * TAG_COPY_HEIGHT_SCALE).abs() < 1e-3);
        assert_eq!(first.base_uv, TAG_ART.base_uv);
    }

    #[test]
    fn boss_copies_hang_under_their_own_slot() {
        let first = copy_at(Anchor::Boss { slot: 0 }, 0);
        assert!((first.fill[0] - 461.85).abs() < 1e-3);
        assert!((first.fill[2] - 1461.85).abs() < 1e-3);
        assert!(first.base[1] > BOSS_ORIGIN[1] + BOSS_ART.fill[3]);
        let upper = copy_at(Anchor::Boss { slot: 1 }, 0);
        assert!((first.base[1] - upper.base[1] - BOSS_ITEM_STEP).abs() < 1e-3);
    }

    #[test]
    fn a_partial_fill_crops_the_art_instead_of_squeezing_it() {
        let copy = copy_at(Anchor::EnemyTag { x: 0.0, y: 0.0 }, 0);
        let (rect, uv) = copy.fill_at(0.5);
        assert!((rect[2] - rect[0] - 68.0).abs() < 1e-3);
        assert!((uv[2] - TAG_ART.fill_uv[2] * 0.5).abs() < 1e-6);
        assert_eq!(uv[1], TAG_ART.fill_uv[1]);
        let (empty, _) = copy.fill_at(-1.0);
        assert_eq!(empty[2], empty[0]);
        let (full, full_uv) = copy.fill_at(2.0);
        assert_eq!(full, copy.fill);
        assert_eq!(full_uv, copy.fill_uv);
    }

    #[test]
    fn uvs_stay_inside_their_images() {
        for art in [TAG_ART, BOSS_ART] {
            for uv in [art.fill_uv, art.base_uv] {
                assert!(uv.iter().all(|v| (0.0..=1.0).contains(v)));
                assert!(uv[0] < uv[2] && uv[1] < uv[3]);
            }
        }
    }

    #[test]
    fn the_tag_base_shows_the_right_end_of_the_base_image_at_half_scale() {
        // Texel 2633 of 2926 is where shape 286's matrix puts the shape's left edge.
        assert!((TAG_BASE_U0 * BASE_IMAGE_SIZE[0] - 2632.9).abs() < 0.5);
        // About half a stage pixel per texel: the 143-pixel shape spans the remaining texels.
        let texels = (1.0 - TAG_BASE_U0) * BASE_IMAGE_SIZE[0];
        let width = TAG_ART.base[2] - TAG_ART.base[0];
        assert!((texels * TAG_BASE_TWIPS_PER_TEXEL / 20.0 - width).abs() < 0.1);
    }

    #[test]
    fn the_stage_scales_uniformly_and_letterboxes() {
        let uhd = StageTransform::for_display([3840.0, 2160.0]).unwrap();
        assert_eq!(uhd.scale, 2.0);
        assert_eq!(uhd.offset, [0.0, 0.0]);
        assert_eq!(uhd.rect([10.0, 20.0, 30.0, 40.0]), [20.0, 40.0, 60.0, 80.0]);
        let ultrawide = StageTransform::for_display([3440.0, 1440.0]).unwrap();
        assert!((ultrawide.scale - 1440.0 / 1080.0).abs() < 1e-6);
        assert!(ultrawide.offset[0] > 0.0 && ultrawide.offset[1] == 0.0);
        assert_eq!(StageTransform::for_display([0.0, 1080.0]), None);
    }
}
