//! Where the game's own HP bar for the locked-on target is, and where the extra bars go under it.
//!
//! Nothing here draws a second HP bar. The game already shows one for the target -- a floating
//! enemy tag over its head, or a boss bar along the bottom -- and the extra bars hang directly
//! under it, at its left edge and width, and vanish whenever the game hides it.
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
//! The geometry is the HUD movie's: `menu/01_000_fe.gfx` has a 1920x1080 stage, places
//! `EnemyTag0..7` and `BossList` on its root timeline, and draws the tag's HP fill with shape 291
//! (1486.9 px wide, 47 tall) scaled `0.0915 x 0.1277` at `x = -2` inside the tag: 136 by 6 px,
//! spanning `-70..66`, framed by shape 286 from `-69.05` to `73.95`, `-7.95..7.55` tall. The boss
//! fill is the same shape scaled `0.6726 x 0.3404` at `x = 14.85` inside `Item_k_0`, which sits at
//! `y = -35 - 55k` inside `BossList` at `(947, 908)`: 1000 by 16 px, so stage `x 461.85..1461.85`.

// Several items here are consumed only by the Windows build; the host build keeps them for tests.
#![cfg_attr(not(windows), allow(dead_code))]

use crate::model::{self, LockState};

/// The HUD movie's stage, in pixels.
pub const STAGE_WIDTH: f32 = 1920.0;
pub const STAGE_HEIGHT: f32 = 1080.0;

/// Left edge and width of the enemy tag's HP fill, relative to the tag's anchor.
pub const TAG_BAR_LEFT: f32 = -70.0;
pub const TAG_BAR_WIDTH: f32 = 136.0;
/// Bottom of the tag's HP frame, relative to the anchor.
pub const TAG_BAR_BOTTOM: f32 = 7.55;

/// The boss HP fill on the stage.
pub const BOSS_BAR_LEFT: f32 = 461.85;
pub const BOSS_BAR_WIDTH: f32 = 1000.0;
/// `BossList` y plus `Item_0_0`'s offset, and the step between stacked boss bars.
pub const BOSS_ITEM0_Y: f32 = 908.0 - 35.0;
pub const BOSS_ITEM_STEP: f32 = 55.0;
/// Half the boss fill's height.
pub const BOSS_BAR_HALF_HEIGHT: f32 = 8.0;

/// Thickness of one extra bar and the gap between bars, in stage pixels, per anchor kind. The
/// tag's own fill is 6 px tall, the boss fill 16; the extra bars are thinner than either so they
/// read as an addition to the game's bar rather than a rival to it.
pub const TAG_EXTRA_HEIGHT: f32 = 3.0;
pub const TAG_EXTRA_GAP: f32 = 1.5;
pub const BOSS_EXTRA_HEIGHT: f32 = 5.0;
pub const BOSS_EXTRA_GAP: f32 = 2.0;

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

/// One extra bar's rectangle on the stage: `[left, top, right, bottom]`.
pub fn bar_rect(anchor: Anchor, index: usize) -> [f32; 4] {
    let (left, width, first_top, height, gap) = match anchor {
        Anchor::EnemyTag { x, y } => (
            x + TAG_BAR_LEFT,
            TAG_BAR_WIDTH,
            y + TAG_BAR_BOTTOM + TAG_EXTRA_GAP,
            TAG_EXTRA_HEIGHT,
            TAG_EXTRA_GAP,
        ),
        Anchor::Boss { slot } => (
            BOSS_BAR_LEFT,
            BOSS_BAR_WIDTH,
            BOSS_ITEM0_Y - BOSS_ITEM_STEP * slot as f32 + BOSS_BAR_HALF_HEIGHT + BOSS_EXTRA_GAP,
            BOSS_EXTRA_HEIGHT,
            BOSS_EXTRA_GAP,
        ),
    };
    let top = first_top + index as f32 * (height + gap);
    [left, top, left + width, top + height]
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
        // The whole HUD hidden.
        assert_eq!(
            pick_anchor(LOCKED, HUD_STATE_HIDE_ALL, &[tag(LOCKED, 1, 1)], &[]),
            None
        );
        // Nothing locked.
        assert_eq!(pick_anchor(u64::MAX, 3, &[tag(u64::MAX, 1, 1)], &[]), None);
        // Another enemy's tag is not the target's.
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
    fn tag_bars_hang_under_the_hp_frame_at_its_left_edge_and_width() {
        let anchor = Anchor::EnemyTag { x: 959.0, y: 567.0 };
        let first = bar_rect(anchor, 0);
        assert_eq!(first[0], 959.0 - 70.0);
        assert_eq!(first[2] - first[0], 136.0);
        assert!(first[1] > 567.0 + TAG_BAR_BOTTOM);
        let second = bar_rect(anchor, 1);
        assert_eq!(second[0], first[0]);
        assert!(second[1] > first[3], "bars must not overlap");
    }

    #[test]
    fn boss_bars_hang_under_their_own_slot() {
        let first = bar_rect(Anchor::Boss { slot: 0 }, 0);
        assert_eq!(first[0], BOSS_BAR_LEFT);
        assert_eq!(first[2], BOSS_BAR_LEFT + 1000.0);
        assert!(first[1] > BOSS_ITEM0_Y + BOSS_BAR_HALF_HEIGHT);
        let upper = bar_rect(Anchor::Boss { slot: 1 }, 0);
        assert_eq!(first[1] - upper[1], BOSS_ITEM_STEP);
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
