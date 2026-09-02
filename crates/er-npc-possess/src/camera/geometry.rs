//! THE SIZE LAW: one creature height in, one camera row out. Pure arithmetic, no game.
//!
//! # Where the anchor numbers come from
//!
//! Every constant below is a value read out of the shipped `regulation.bin`, not a taste
//! judgement, and each one is named with the row it came from so the next reader can check it
//! with `scripts/er-param-read.py` rather than trust this comment:
//!
//! | constant | value | source |
//! |---|---|---|
//! | [`PLAYER_HIT_HEIGHT`] | 1.5 | `NpcParam` row 0 (`c0000`, the player) `hitHeight` |
//! | [`PLAYER_CAM_DIST`] | 3.8 | `LockCamParam` row 0 `camDistTarget` |
//! | [`PLAYER_PIVOT`] | 1.45 | `LockCamParam` row 0 `chrOrgOffset_Y` |
//! | [`PLAYER_PITCH_MIN_DEG`] | -40.0 | `LockCamParam` row 0 `rotRangeMinX` |
//!
//! Those four are ONE data point -- "the camera FromSoftware gives a subject 1.5 m tall" -- and
//! it is the only one the game contains, which is why the rest of this module is an extrapolation
//! from it rather than a fit.
//!
//! # The dataset that looks like it should have decided this, and why it did not
//!
//! Joining every `NpcParam` row that names a `lockCameraParamId` onto that `LockCamParam` row
//! gives 155 hand-tuned (creature height -> camera) pairs, and fitting them yields
//! `camDistTarget ~ 3.75 * H^0.10` with `chrOrgOffset_Y` almost flat at 1.3-2.15 m across a range
//! running from 0.6 m to 42 m. Reading that as the size law would be a category error: it would
//! put a Fire Giant's camera 5.2 m out where this module puts it 30 m out, a factor of six.
//!
//! Those rows describe **a 1.5 m player fighting an H-metre TARGET**, not an H-metre subject.
//! `CS::ChrExFollowCam::ApplyZoomLerp` applies `chrOrgOffset_Y` to the camera's SUBJECT, and the
//! subject in vanilla is always the player -- which is exactly why the offset stays at human chest
//! height however big the thing being fought is. The row is *selected* by the target and *applied*
//! to the subject. Possession swaps the subject, so the selection side of that dataset says
//! nothing about what we need.
//!
//! What it does confirm, and what this module follows: the pitch minimum genuinely does relax with
//! size (median `rotRangeMinX` is -40 below 4 m and -20 to -30 above it), and `camFovY` does not
//! move with size at all (48-50 across the entire range), which is why this module leaves the FOV
//! alone. Where this module's single-knee ramp differs from vanilla it is CONSERVATIVE: it reaches
//! -15 at 9 m where the vanilla rows are already at -20 by 4 m.
//!
//! # The law
//!
//! With `s = H / 1.5` the creature's height as a multiple of the player's:
//!
//! * **distance** `3.8 * s^exponent * per_chr_scale`, clamped to the configured ceiling and then
//!   raised, if it has to be, to `hitRadius * 1.25`. The clearance floor is applied LAST and wins
//!   over the ceiling on purpose: a camera outside a 40 m box is a bad shot, a camera inside the
//!   model is not a shot at all.
//! * **pivot height** `H * fraction`, where the fraction runs from the vanilla 1.45/1.5 = 0.967
//!   (head height, what the player gets) down to [`CHEST_FRACTION`] once the body is
//!   [`LARGE_SCALE`] times player size. A dragon framed on its head has its whole body off the
//!   bottom of the screen; a human framed on the chest looks like the camera slipped.
//! * **pitch minimum** lerped -40 -> [`LARGE_PITCH_MIN_DEG`] over the same knee.
//!
//! At exactly player size all three reproduce `LockCamParam` row 0 to the float, which is the
//! invariant [`tests::at_player_size_the_derived_row_is_the_vanilla_row`] pins. That is the whole
//! point of anchoring on a vanilla row rather than picking round numbers: possessing something
//! human-sized must look like nothing happened.

// Pure arithmetic; ungated so `cargo test` proves it on the host with no game running.
#![cfg_attr(not(windows), allow(dead_code))]

use crate::settings::CameraSettings;

/// `NpcParam` row 0 (`c0000`) `hitHeight` -- the player's physics capsule, in metres. Every
/// creature's size is expressed as a multiple of this.
pub(crate) const PLAYER_HIT_HEIGHT: f32 = 1.5;
/// `LockCamParam` row 0 `camDistTarget`.
pub(crate) const PLAYER_CAM_DIST: f32 = 3.8;
/// `LockCamParam` row 0 `chrOrgOffset_Y` -- the camera pivot, roughly head height on a 1.5 m body.
pub(crate) const PLAYER_PIVOT: f32 = 1.45;
/// `LockCamParam` row 0 `rotRangeMinX`, in degrees. The pitch MINIMUM only; the maximum lives at
/// `ChrExFollowCam+0x2d4` and is fixed at construction, which this layer does not touch.
pub(crate) const PLAYER_PITCH_MIN_DEG: f32 = -40.0;

/// The pitch minimum a fully-grown subject gets. Vanilla's own target-side rows sit at -20 above
/// 4 m and -12..-15 for the biggest, so this is the relaxed end of the range the game itself uses.
pub(crate) const LARGE_PITCH_MIN_DEG: f32 = -15.0;
/// Where the camera pivots on a body too big to frame on the head: the chest.
///
/// 0.65 is the median height fraction of dummy poly 220 across the 422-FLVER chr corpus recorded
/// in `bd possession-camera-size-adaptation-levers-2026-09-01` -- i.e. where the engine's own
/// "chest" marker sits. The dummy itself is NOT used (see [`crate::camera`] for why); only the
/// measurement of where a chest is.
pub(crate) const CHEST_FRACTION: f32 = 0.65;
/// How many times player size counts as "fully grown", i.e. where the pivot fraction and the pitch
/// minimum stop moving. 6 x 1.5 m = 9 m, a little over a Troll.
pub(crate) const LARGE_SCALE: f32 = 6.0;
/// How much further out than the creature's own physics radius the camera must sit. The radius is
/// `CSChrPhysicsModule+0x344`, the horizontal half-extent of the capsule the world collides with,
/// so anything at or inside it is inside the model.
pub(crate) const RADIUS_CLEARANCE: f32 = 1.25;
/// Nothing is framed from closer than this, whatever the per-chr scale says.
pub(crate) const MIN_DISTANCE: f32 = 0.5;
/// Heights above this are a broken or modded row rather than a creature -- the largest thing the
/// shipped regulation contains is 59 m (`c4450`). Clamped rather than refused, because a possession
/// that works with an odd camera beats one that refuses over a number nobody will ever see.
pub(crate) const MAX_PLAUSIBLE_HEIGHT: f32 = 200.0;

/// The three `LockCamParam` fields the size law decides.
///
/// Everything else in the row -- FOV, the lock vertical offset, the chase rate, the lock-on
/// radii -- is copied from the base row untouched; see [`crate::camera`].
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Shape {
    /// `camDistTarget`, metres.
    pub(crate) distance: f32,
    /// `chrOrgOffset_Y`, metres above the character's origin.
    pub(crate) pivot_height: f32,
    /// `rotRangeMinX`, degrees. Negative.
    pub(crate) pitch_min_deg: f32,
}

/// Why a possession is being framed with the vanilla camera instead of an adapted one.
///
/// Every one of these is an ordinary outcome rather than an error: the feature is off, the build
/// is one nobody measured, the creature has no readable size, or the row the player picked is not
/// free. All of them end with the camera behaving exactly as it did before this layer existed,
/// and all of them are named in `er-npc-possess.derived.toml` so "my camera did not change" has an
/// answer that is not "read the source".
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Refusal {
    /// `[camera].enabled = false`.
    Disabled,
    /// The running build is neither 1.16.2 nor 1.17, so no offset table applies.
    UnmeasuredBuild,
    /// `CSChrPhysicsModule+0x340` did not read, or read as zero/NaN.
    NoHeight,
    /// `SoloParamRepository` is not up, or its `LockCamParam` / `NpcParam` holder has not
    /// streamed in yet.
    ParamsNotReady,
    /// A `NpcParam.lockCameraParamId` or `RideParam.rideCamParamId` in the LIVE regulation names
    /// the row `[camera].param_row` picked, so patching it would change some other character's
    /// camera. Carries that row.
    RowInUse(u32),
    /// `[camera].param_row` is not a row of `LockCamParam` at all. A missing id makes
    /// `LookupLockCamParam` return NULL and `ApplyZoomLerp` do nothing whatsoever -- the camera
    /// freezes at its current values with no crash -- so this must be caught rather than written.
    RowMissing(u32),
    /// `LockCamParam` row 0 is gone, so there is no vanilla base to copy the untouched fields from.
    BaseRowMissing,
    /// `WorldChrMan+0x1ece0 chrCam` or `ChrCam+0x60 chrExFollowCam` did not resolve.
    NoFollowCam,
    /// `ChrExFollowCam+0x468` would not take the write.
    WriteFailed,
}

impl Refusal {
    /// One line for the log and for the derived file.
    pub(crate) fn describe(self) -> String {
        match self {
            Self::Disabled => "[camera].enabled is false".to_owned(),
            Self::UnmeasuredBuild => {
                "this build is neither 1.16.2 nor 1.17, and the camera offsets were only measured \
                 on those two"
                    .to_owned()
            }
            Self::NoHeight => {
                "the creature's CSChrPhysicsModule+0x340 hitHeight did not read as a positive \
                 number"
                    .to_owned()
            }
            Self::ParamsNotReady => "SoloParamRepository has not streamed the params in".to_owned(),
            Self::RowInUse(row) => format!(
                "LockCamParam row {row} is referenced by this regulation, so patching it would \
                 move some other character's camera -- pick a free row with [camera].param_row"
            ),
            Self::RowMissing(row) => format!(
                "LockCamParam has no row {row}, and a missing id makes the engine's ApplyZoomLerp \
                 do nothing at all rather than fall back"
            ),
            Self::BaseRowMissing => {
                "LockCamParam row 0, the player's own camera row this one is derived from, is \
                 missing"
                    .to_owned()
            }
            Self::NoFollowCam => {
                "WorldChrMan+0x1ece0 chrCam or ChrCam+0x60 chrExFollowCam did not resolve"
                    .to_owned()
            }
            Self::WriteFailed => "ChrExFollowCam+0x468 would not take the write".to_owned(),
        }
    }

    /// A short token for the derived file's key, so a reader can grep for it.
    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::Disabled => "disabled",
            Self::UnmeasuredBuild => "unmeasured-build",
            Self::NoHeight => "no-height",
            Self::ParamsNotReady => "params-not-ready",
            Self::RowInUse(_) => "row-in-use",
            Self::RowMissing(_) => "row-missing",
            Self::BaseRowMissing => "base-row-missing",
            Self::NoFollowCam => "no-follow-cam",
            Self::WriteFailed => "write-failed",
        }
    }
}

/// What happened to the camera for one possession, in the terms the derived file prints.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Report {
    /// `CSChrPhysicsModule+0x340`, or `None` when it did not read.
    pub(crate) hit_height: Option<f32>,
    /// `CSChrPhysicsModule+0x344`.
    pub(crate) hit_radius: Option<f32>,
    /// The row `[camera].param_row` named.
    pub(crate) row: u32,
    /// The row the untouched fields were copied from -- whatever the camera resolved last frame,
    /// or 0 when that was unreadable.
    pub(crate) base_row: Option<u32>,
    /// The per-chr `camera_distance_scale` in force.
    pub(crate) distance_scale: f32,
    /// What was written, when something was.
    pub(crate) applied: Option<Shape>,
    /// Why nothing was, when nothing was.
    pub(crate) refusal: Option<Refusal>,
}

impl Report {
    /// A report for a possession that never got as far as reading a size.
    pub(crate) const fn refused(row: u32, distance_scale: f32, refusal: Refusal) -> Self {
        Self {
            hit_height: None,
            hit_radius: None,
            row,
            base_row: None,
            distance_scale,
            applied: None,
            refusal: Some(refusal),
        }
    }
}

/// Linear interpolation, `t` already clamped to `0..=1` by the caller.
fn lerp(from: f32, to: f32, t: f32) -> f32 {
    (to - from).mul_add(t, from)
}

/// How far along the player-to-fully-grown ramp a subject of scale `s` sits.
fn ramp(scale: f32) -> f32 {
    ((scale - 1.0) / (LARGE_SCALE - 1.0)).clamp(0.0, 1.0)
}

/// The camera for a subject `hit_height` metres tall and `hit_radius` metres wide.
///
/// `None` when the height is not a usable number -- three shipped `NpcParam` rows carry
/// `hitHeight = 0.0`, and a modded one could carry anything -- in which case the caller leaves the
/// camera alone rather than dividing by it.
pub(crate) fn shape(
    hit_height: f32,
    hit_radius: f32,
    distance_scale: f32,
    settings: CameraSettings,
) -> Option<Shape> {
    if !hit_height.is_finite() || hit_height <= 0.0 {
        return None;
    }
    let height = hit_height.min(MAX_PLAUSIBLE_HEIGHT);
    // A negative or NaN radius is treated as no radius rather than refusing: the clearance floor
    // is a safety net, and losing the net is not a reason to lose the camera.
    let radius = if hit_radius.is_finite() && hit_radius > 0.0 {
        hit_radius
    } else {
        0.0
    };
    let scale = height / PLAYER_HIT_HEIGHT;
    let t = ramp(scale);

    // `powf` on a non-finite exponent would poison everything downstream; the settings parser
    // already rejects those, and this is the second belt.
    let exponent = if settings.distance_exponent.is_finite() {
        settings.distance_exponent
    } else {
        0.7
    };
    let per_chr = if distance_scale.is_finite() && distance_scale > 0.0 {
        distance_scale
    } else {
        1.0
    };
    let ceiling = if settings.distance_max.is_finite() && settings.distance_max > MIN_DISTANCE {
        settings.distance_max
    } else {
        f32::MAX
    };
    let distance = (PLAYER_CAM_DIST * scale.powf(exponent) * per_chr)
        .clamp(MIN_DISTANCE, ceiling)
        // LAST, and above the ceiling. See the module docs.
        .max(radius * RADIUS_CLEARANCE);

    // At `t == 0` this is `PLAYER_PIVOT * scale`, which is exactly 1.45 for the player rather than
    // 1.45 rounded through a fraction -- the invariant the tests pin.
    let pivot_height = lerp(PLAYER_PIVOT * scale, CHEST_FRACTION * height, t);
    let pitch_min_deg = lerp(PLAYER_PITCH_MIN_DEG, LARGE_PITCH_MIN_DEG, t);

    Some(Shape {
        distance,
        pivot_height,
        pitch_min_deg,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings() -> CameraSettings {
        CameraSettings::default()
    }

    /// THE ANCHOR. A subject the size of the player must come out as `LockCamParam` row 0, to the
    /// float -- otherwise possessing a human-sized NPC would visibly move the camera for no
    /// reason, and the whole extrapolation would be resting on nothing.
    #[test]
    fn at_player_size_the_derived_row_is_the_vanilla_row() {
        // c0000's own `NpcParam` row: hitHeight 1.5, hitRadius 0.4.
        let shape = shape(PLAYER_HIT_HEIGHT, 0.4, 1.0, settings()).expect("a real height");
        assert_eq!(shape.distance, PLAYER_CAM_DIST);
        assert_eq!(shape.pivot_height, PLAYER_PIVOT);
        assert_eq!(shape.pitch_min_deg, PLAYER_PITCH_MIN_DEG);
    }

    /// The clearance floor never lets the camera sit inside the physics capsule, for any of the
    /// creatures the shipped regulation actually contains.
    #[test]
    fn the_camera_clears_every_shipped_creatures_own_radius() {
        // (chr, hitHeight, hitRadius) straight out of `NpcParam`, biggest first.
        const CREATURES: [(u32, f32, f32); 8] = [
            (4450, 59.0, 18.0),
            (4504, 42.0, 17.0),
            (4760, 29.0, 10.0),
            (4501, 14.0, 14.0),
            (4500, 12.0, 12.0),
            (4520, 10.0, 10.0),
            (4600, 7.2, 1.8),
            (5210, 2.4, 3.5),
        ];
        for (chr, height, radius) in CREATURES {
            let shape = shape(height, radius, 1.0, settings()).expect("a real height");
            assert!(
                shape.distance > radius,
                "c{chr}: camera at {} is inside a {radius} m radius",
                shape.distance
            );
        }
    }

    /// The clearance floor beats the ceiling, because a camera inside the model is not a shot.
    #[test]
    fn the_radius_floor_wins_over_the_distance_ceiling() {
        let tight = CameraSettings {
            distance_max: 6.0,
            ..settings()
        };
        // c4504: 42 m tall, 17 m wide. The ceiling would put the camera 6 m out, well inside it.
        let shape = shape(42.0, 17.0, 1.0, tight).expect("a real height");
        assert_eq!(shape.distance, 17.0 * RADIUS_CLEARANCE);
    }

    /// Bigger is further away and pivots higher, monotonically, over the whole shipped range.
    #[test]
    fn distance_and_pivot_rise_with_size() {
        let heights = [0.6_f32, 1.0, 1.5, 1.9, 3.6, 7.2, 12.0, 29.0, 59.0];
        let mut previous: Option<Shape> = None;
        for height in heights {
            // Radius held at zero so this measures the size law alone, not the clearance floor.
            let shape = shape(height, 0.0, 1.0, settings()).expect("a real height");
            if let Some(before) = previous {
                assert!(
                    shape.distance > before.distance,
                    "{height} m: {} is not further than {}",
                    shape.distance,
                    before.distance
                );
                assert!(
                    shape.pivot_height > before.pivot_height,
                    "{height} m: pivot {} is not above {}",
                    shape.pivot_height,
                    before.pivot_height
                );
            }
            previous = Some(shape);
        }
    }

    /// The pitch minimum relaxes with size and then stops, and never passes the vanilla end or the
    /// large end of the range.
    #[test]
    fn the_pitch_minimum_relaxes_once_and_then_holds() {
        let small = shape(PLAYER_HIT_HEIGHT, 0.0, 1.0, settings()).expect("h");
        let mid = shape(4.5, 0.0, 1.0, settings()).expect("h");
        let large = shape(9.0, 0.0, 1.0, settings()).expect("h");
        let huge = shape(59.0, 0.0, 1.0, settings()).expect("h");
        assert_eq!(small.pitch_min_deg, PLAYER_PITCH_MIN_DEG);
        assert!(small.pitch_min_deg < mid.pitch_min_deg);
        assert!(mid.pitch_min_deg < large.pitch_min_deg);
        assert_eq!(large.pitch_min_deg, LARGE_PITCH_MIN_DEG);
        assert_eq!(huge.pitch_min_deg, LARGE_PITCH_MIN_DEG);
    }

    /// The pivot fraction ends at the chest rather than the head once the body is big.
    #[test]
    fn a_big_body_is_framed_on_the_chest() {
        let huge = shape(29.0, 0.0, 1.0, settings()).expect("h");
        assert_eq!(huge.pivot_height, CHEST_FRACTION * 29.0);
        // ...and a Fire Giant framed on its head would be a pivot half again as high.
        assert!(huge.pivot_height < 29.0 * (PLAYER_PIVOT / PLAYER_HIT_HEIGHT));
    }

    /// The three shipped `NpcParam` rows with `hitHeight = 0.0` -- and anything a modded
    /// regulation could put there -- refuse rather than divide by it.
    #[test]
    fn an_unusable_height_yields_nothing_at_all() {
        for height in [0.0_f32, -1.0, f32::NAN, f32::INFINITY, f32::NEG_INFINITY] {
            assert_eq!(shape(height, 1.0, 1.0, settings()), None, "{height}");
        }
    }

    /// An absurd modded height is clamped rather than refused, and still produces finite numbers.
    #[test]
    fn an_absurd_height_is_clamped_and_stays_finite() {
        let shape = shape(1.0e9, 0.0, 1.0, settings()).expect("clamped, not refused");
        assert!(shape.distance.is_finite());
        assert!(shape.pivot_height.is_finite());
        assert_eq!(shape.pivot_height, CHEST_FRACTION * MAX_PLAUSIBLE_HEIGHT);
        assert!(shape.distance <= CameraSettings::default().distance_max);
    }

    /// A `distance_max` below the hard floor is ignored rather than made to panic.
    ///
    /// `f32::clamp` panics when its minimum exceeds its maximum, and the settings parser accepts
    /// any positive number -- so `distance_max = 0.1` reaches this function and must not take the
    /// game down over a config typo.
    #[test]
    fn a_ceiling_below_the_hard_floor_is_ignored_rather_than_a_panic() {
        let silly = CameraSettings {
            distance_max: 0.1,
            ..settings()
        };
        let ignored = shape(12.0, 0.0, 1.0, silly).expect("a real height");
        let plain = shape(12.0, 0.0, 1.0, settings()).expect("a real height");
        assert!(ignored.distance > MIN_DISTANCE);
        assert_eq!(ignored.distance, plain.distance);
    }

    /// A junk radius costs the clearance floor and nothing else.
    #[test]
    fn a_junk_radius_does_not_poison_the_distance() {
        let clean = shape(12.0, 0.0, 1.0, settings()).expect("a real height");
        for radius in [f32::NAN, -3.0, f32::INFINITY] {
            let junk = shape(12.0, radius, 1.0, settings()).expect("a real height");
            assert!(junk.distance.is_finite(), "{radius}");
            assert_eq!(junk.distance, clean.distance, "{radius}");
        }
    }

    /// `camera_distance_scale` multiplies the law and can pull the camera in as well as push it
    /// out, which is what a player tuning a quadruped needs.
    #[test]
    fn the_per_chr_scale_moves_the_distance_both_ways() {
        let plain = shape(12.0, 0.0, 1.0, settings()).expect("h");
        let out = shape(12.0, 0.0, 2.0, settings()).expect("h");
        let close = shape(12.0, 0.0, 0.5, settings()).expect("h");
        assert!((out.distance - plain.distance * 2.0).abs() < 1.0e-3);
        assert!((close.distance - plain.distance * 0.5).abs() < 1.0e-3);
        // ...and it must not move the framing, only the distance.
        assert_eq!(out.pivot_height, plain.pivot_height);
        assert_eq!(close.pitch_min_deg, plain.pitch_min_deg);
    }

    /// A junk `distance_scale` falls back to 1.0 rather than producing a NaN camera.
    #[test]
    fn a_junk_per_chr_scale_falls_back_to_one() {
        let plain = shape(12.0, 0.0, 1.0, settings()).expect("h");
        for scale in [f32::NAN, 0.0, -2.0] {
            assert_eq!(
                shape(12.0, 0.0, scale, settings()).expect("h").distance,
                plain.distance,
                "{scale}"
            );
        }
    }

    /// A zero exponent turns the size law off without turning the clearance floor off -- the one
    /// setting combination that could otherwise put a camera inside a dragon.
    #[test]
    fn a_flat_exponent_still_clears_the_body() {
        let flat = CameraSettings {
            distance_exponent: 0.0,
            ..settings()
        };
        let shape = shape(12.0, 12.0, 1.0, flat).expect("h");
        assert_eq!(shape.distance, 12.0 * RADIUS_CLEARANCE);
    }

    /// Every refusal spells itself differently, so a derived file naming one is unambiguous.
    #[test]
    fn every_refusal_has_its_own_name_and_a_sentence() {
        let all = [
            Refusal::Disabled,
            Refusal::UnmeasuredBuild,
            Refusal::NoHeight,
            Refusal::ParamsNotReady,
            Refusal::RowInUse(1000),
            Refusal::RowMissing(1000),
            Refusal::BaseRowMissing,
            Refusal::NoFollowCam,
            Refusal::WriteFailed,
        ];
        let mut names: Vec<&str> = all.iter().copied().map(Refusal::name).collect();
        let count = names.len();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), count, "{names:?}");
        for refusal in all {
            let sentence = refusal.describe();
            assert!(sentence.len() > 20, "{refusal:?}: {sentence}");
        }
    }
}
