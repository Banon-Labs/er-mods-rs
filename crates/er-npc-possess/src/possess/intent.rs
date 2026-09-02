//! WHERE THE POSSESSED CREATURE IS TOLD TO GO, and the four fields that takes.
//!
//! # The one field that decides whether anything happens
//!
//! This module used to emit two writes and the creature did not move. The reason was not the two
//! it emitted; it was the two it did not. `[vt+0x50]` (`FUN_1403d0250`) -- the slot we forward,
//! because it is the one that moves the body -- computes the frame's move vector inside
//!
//! ```text
//! if (aiIns->walkType != 0 && ChrIns::GetMoveType(chr) != 0) { ... wantToMoveTo - riderPos ... }
//! ```
//!
//! and otherwise leaves it `DL_ZERO_VECTOR`. `walkType` is written by `CSAiFunc::MoveTo`, by
//! `ClearMoveRequest`, and by the AI goal bodies -- all of them downstream of the goal selection
//! this crate no-ops at `[vt+0x48]`. So a possessed creature's `walkType` is frozen at whatever
//! the last goal left, which for a fresh spawn is `0`: a perfect `wantToMoveTo`, written every
//! frame, consumed by a branch that never runs.
//!
//! `GetMoveType` is the other half of that gate and is NOT ours to set -- it comes from the
//! creature's own `NpcParam` row, so a creature the game ships as stationary stays stationary and
//! that is correct. See [`crate::possess::layout::ai_ins`] for the byte proof of all of it.
//!
//! # The race this module still has to lose safely
//!
//! `[vt+0x50]` runs `AiIns::UpdateMovement` before reading any of this, and that function branches
//! three ways over `wantToMoveTo`:
//!
//! ```text
//! if HasFollowPathMoveTarget(pathData) && <a second path predicate> && !IsArrived()
//!         wantToMoveTo = own physics position          // "stop"
//! else if HasFollowPathMoveTarget(pathData) && !aiIns[0xe990] && !IsArrived()
//!         wantToMoveTo = pathData->target              // the path wins
//! else    wantToMoveTo unchanged                        // OUR value survives
//! ```
//!
//! A possessed creature has no follow-path target -- building one is `FUN_1402c65e0`, the third
//! thing `MoveTo` does and the one this crate does NOT reproduce -- so both predicates are false
//! and the third branch is the live one. [`IntentWrite`] still emits `pathData->target` as well as
//! `wantToMoveTo`, with the same value, because the two can never disagree when they are one
//! number written twice and it is the branch-two answer for free. Only branch one still escapes
//! us, and no field write reaches it.
//!
//! # `turnTarget` IS a steering wheel, in exactly one of its values
//!
//! An earlier version of this note said writing `turnTarget` steers nothing, because the named
//! points an `AiTargetPointType` selects stop being refreshed once goal selection is dead. That is
//! true of every value except `TARGET_SELF`, which is refreshed from a field we write ourselves.
//! `UpdateMovement` ends with `FUN_1402c9410(aiIns, aiIns->turnTarget)`, whose `TARGET_SELF`
//! branch takes `wantToMoveTo - GetPhysicsPosition()` as the direction to face and stores the
//! resulting angles where `[vt+0x50]` differences them against the body's live orientation.
//!
//! So `TARGET_SELF` means "face wherever you have been told to walk". The body still converges at
//! its own `NpcParam` turn rate -- that is the locomotion executor's business and we do not touch
//! it -- but it now converges on something the player chose.
//!
//! # Nothing here writes a velocity
//!
//! The move vector `[vt+0x50]` builds is a normalised DIRECTION, transformed by the body's model
//! matrix and handed to `CSChrActionRequestModule` -- the same request module the player's own pad
//! feeds. The behaviour graph turns that into locomotion clips and their root motion moves the
//! body. There is no velocity anywhere on the path, which is why `[movement]` no longer carries a
//! `root_motion_only` switch: it was never a choice.

// Pure math; stays ungated so its tests run on the host.
#![cfg_attr(not(windows), allow(dead_code))]

use crate::possess::layout::ai_ins;

/// How far ahead of the creature the move target is placed, in physics-space units, at full stick
/// deflection and `speed_scale = 1.0`.
///
/// It is a LEASH, not a speed: the engine walks toward the point and stops on arrival, so a short
/// reach means a creature that keeps arriving and re-departing (visible stutter) and a long one
/// means a creature that keeps running for a while after the stick is released. Eight units is
/// roughly two of the range bands' "close" figure and holds a continuous walk at 60fps with a
/// wide margin.
pub(crate) const REACH: f32 = 8.0;

/// Stick deflection below this is no input at all.
///
/// Separate from `[movement].turn_deadzone_deg`, which is about how far off-heading counts as
/// asking for a turn. This one is about the hardware: an XInput stick at rest reports a few
/// hundred counts of noise, and without a floor the creature would creep.
pub(crate) const STICK_DEADZONE: f32 = 0.25;

/// Deflection at or below this walks; above it runs.
///
/// The engine offers exactly two moving gaits -- `CSAiFunc::MoveTo` writes `2 - walk`, so the pair
/// is `{WALK_TYPE_WALK, WALK_TYPE_RUN}` and there is no third -- and `[vt+0x50]` scales the move
/// vector down for the walk value only. Half deflection is the split because it is the one place
/// on a stick a player can find without looking, and because a keyboard synthesises full
/// deflection, so WASD runs.
const RUN_DEFLECTION: f32 = 0.5;

/// Full deflection on an XInput thumbstick axis. The negative extreme is `-32768`, which is why
/// the normalisation clamps rather than dividing by the magnitude of whichever end it got.
const XINPUT_AXIS_FULL: f32 = 32767.0;

/// A normalised stick reading: `x` right-positive, `y` forward-positive, magnitude at most 1.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct Stick {
    pub(crate) x: f32,
    pub(crate) y: f32,
}

impl Stick {
    /// Normalise a raw XInput axis pair, or `None` when it is inside the deadzone.
    ///
    /// Radial rather than per-axis: a per-axis deadzone leaves a cross-shaped dead region, so a
    /// diagonal push just past the corner reads as a pure-axis push and the creature walks at 45
    /// degrees to where the stick is pointing.
    #[must_use]
    pub(crate) fn from_xinput(raw_x: i16, raw_y: i16) -> Option<Self> {
        Self::from_axes(
            f32::from(raw_x) / XINPUT_AXIS_FULL,
            f32::from(raw_y) / XINPUT_AXIS_FULL,
        )
    }

    /// Normalise an already-unit-scaled axis pair, or `None` inside the deadzone.
    #[must_use]
    pub(crate) fn from_axes(x: f32, y: f32) -> Option<Self> {
        if !x.is_finite() || !y.is_finite() {
            return None;
        }
        let magnitude = x.hypot(y);
        if magnitude < STICK_DEADZONE {
            return None;
        }
        // Clamp rather than always normalising: a half-pushed stick should mean a half-length
        // request, and only the corners of a square-gated stick exceed 1.
        let scale = if magnitude > 1.0 {
            1.0 / magnitude
        } else {
            1.0
        };
        Some(Self {
            x: x * scale,
            y: y * scale,
        })
    }

    /// How far the stick is pushed, `0.0..=1.0`. Decides walk versus run.
    #[must_use]
    fn magnitude(self) -> f32 {
        self.x.hypot(self.y)
    }

    /// The angle off the body's own forward, in radians, `-PI..=PI`.
    ///
    /// The stick is read CREATURE-RELATIVE -- `y` is the body's forward and `x` its right -- so
    /// "how far off-heading is this push" needs no yaw and no world transform; it is the stick's
    /// own angle. That is the number `[movement].turn_deadzone_deg` is expressed in.
    #[must_use]
    fn off_heading(self) -> f32 {
        self.x.atan2(self.y)
    }
}

/// The set of writes one frame of movement intent turns into.
///
/// A value rather than four calls, so "the same number goes to both target fields" and "a frame
/// with no stick writes the stop gait" are things that exist and can be tested rather than a
/// discipline expected of the caller.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct IntentWrite {
    /// The physics-space point to walk to. Written to `AiIns.wantToMoveTo` AND to
    /// `AiIns.pathData->target`.
    pub(crate) target: [f32; 3],
    /// `AiIns.walkType`. **THE GATE**: `0` and the engine builds no move vector at all, whatever
    /// `target` says. `1` walks, `2` runs.
    pub(crate) walk_type: i32,
    /// `AiIns.turnTarget`, always [`TURN_TARGET_SELF`] -- it means "face wherever `wantToMoveTo`
    /// is", and when `walk_type` is the stop the same branch holds the body's current facing
    /// instead, so one constant covers both moving and stopped.
    ///
    /// [`TURN_TARGET_SELF`]: crate::possess::layout::ai_ins::TURN_TARGET_SELF
    pub(crate) turn_target: i32,
}

impl IntentWrite {
    /// Is this frame asking for movement at all?
    #[must_use]
    pub(crate) const fn moving(self) -> bool {
        self.walk_type != ai_ins::WALK_TYPE_STOP
    }

    /// The frame that stops the creature where it stands.
    ///
    /// Exactly what `CS::AiIns::ClearMoveRequest` writes -- `walkType = 0` and
    /// `wantToMoveTo = own physics position` -- so releasing the stick leaves the AI in the state
    /// the engine's own "stop" leaves it in rather than in one only this mod produces. Writing the
    /// creature's own position also reads as "arrived", which is what stops a stale target from
    /// walking the body on after the player let go.
    #[must_use]
    pub(crate) const fn stopped(at: [f32; 3]) -> Self {
        Self {
            target: at,
            walk_type: ai_ins::WALK_TYPE_STOP,
            turn_target: ai_ins::TURN_TARGET_SELF,
        }
    }
}

/// Turn a stick reading into the frame's intent.
///
/// `yaw` is the creature's own heading in radians, taken from
/// `CSChrPhysicsModule.orientationEuler.y`. The basis is `forward = (sin yaw, 0, cos yaw)` and
/// `right = (cos yaw, 0, -sin yaw)`, i.e. yaw 0 faces `+Z` and a quarter turn faces `+X` -- the
/// right-handed, Y-up convention `EulerToQuat` uses on the other side of this same field.
///
/// Creature-relative rather than camera-relative on purpose, and it costs nothing in feel: the
/// camera is a follow camera behind the possessed body, so its forward and the body's forward
/// agree in the steady state, and the two converge whenever they do not.
///
/// `turn_deadzone_deg` is `[movement].turn_deadzone_deg`: a push closer to straight ahead than
/// this is treated as EXACTLY straight ahead. The engine derives the body's facing from the target
/// this function returns, so without a floor a stick a couple of degrees off centre is a standing
/// request to turn, and the body weaves down a corridor it was asked to walk straight along.
/// Snapping to the heading makes the derived turn delta exactly zero.
#[must_use]
pub(crate) fn intent(
    position: [f32; 3],
    yaw: f32,
    stick: Option<Stick>,
    speed_scale: f32,
    turn_deadzone_deg: f32,
) -> IntentWrite {
    let Some(stick) = stick else {
        return IntentWrite::stopped(position);
    };
    if !yaw.is_finite() || !speed_scale.is_finite() || speed_scale <= 0.0 {
        return IntentWrite::stopped(position);
    }
    // Inside the deadzone the push becomes pure forward at the same magnitude, so the gait below
    // is unaffected and only the heading request is straightened.
    let magnitude = stick.magnitude();
    let deadzone = turn_deadzone_deg.to_radians();
    let stick = if deadzone.is_finite() && stick.off_heading().abs() <= deadzone {
        Stick {
            x: 0.0,
            y: magnitude,
        }
    } else {
        stick
    };
    let (sin, cos) = yaw.sin_cos();
    // forward * stick.y + right * stick.x
    let dx = sin.mul_add(stick.y, cos * stick.x);
    let dz = cos.mul_add(stick.y, -sin * stick.x);
    let reach = REACH * speed_scale;
    IntentWrite {
        target: [
            reach.mul_add(dx, position[0]),
            position[1],
            reach.mul_add(dz, position[2]),
        ],
        walk_type: if magnitude > RUN_DEFLECTION {
            ai_ins::WALK_TYPE_RUN
        } else {
            ai_ins::WALK_TYPE_WALK
        },
        turn_target: ai_ins::TURN_TARGET_SELF,
    }
}

/// A point `distance` in front of a character facing `yaw`, at the same height.
///
/// THE SAME BASIS AS [`intent`], and that is the whole reason it lives here rather than beside its
/// caller: `forward = (sin yaw, 0, cos yaw)`. A second copy of that convention, written from the
/// same description, is exactly how a sign error gets in -- and the failure would be a creature
/// spawned behind the player, which reads as "the spawn did nothing".
///
/// Height is COPIED, never offset. A spawn point raised off the player's own footing is a creature
/// dropped from a height, and the ground under it is not known here.
#[must_use]
pub(crate) fn ahead_of(position: [f32; 3], yaw: f32, distance: f32) -> [f32; 3] {
    if !yaw.is_finite() || !distance.is_finite() {
        return position;
    }
    let (sin, cos) = yaw.sin_cos();
    [
        distance.mul_add(sin, position[0]),
        position[1],
        distance.mul_add(cos, position[2]),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Within a millimetre, which is far below anything the engine can act on.
    fn close(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-3
    }

    /// [`intent`] with the turn deadzone OFF.
    ///
    /// Every test that is not about the deadzone uses this, so a widened default can never
    /// silently straighten a push a basis test meant to be off-heading -- which would turn a real
    /// sign error into a passing test.
    fn drive(position: [f32; 3], yaw: f32, stick: Option<Stick>, speed_scale: f32) -> IntentWrite {
        intent(position, yaw, stick, speed_scale, 0.0)
    }

    /// The spawn point uses the SAME basis as the movement target, and in front means in front.
    /// A sign error here spawns the creature behind the player, which is indistinguishable from
    /// the spawn having done nothing.
    #[test]
    fn ahead_of_agrees_with_the_movement_basis_and_never_changes_height() {
        let at = [10.0, 5.0, -20.0];
        // Yaw 0 faces +Z, which is what `intent` says and what `EulerToQuat` uses.
        let north = ahead_of(at, 0.0, 3.0);
        assert!(close(north[0], 10.0), "{north:?}");
        assert!(close(north[1], 5.0), "height is copied, not offset");
        assert!(close(north[2], -17.0), "{north:?}");
        // A quarter turn faces +X.
        let east = ahead_of(at, core::f32::consts::FRAC_PI_2, 3.0);
        assert!(close(east[0], 13.0), "{east:?}");
        assert!(close(east[2], -20.0), "{east:?}");
        // ...and it is the same direction `intent` walks toward on full forward stick.
        let walked = drive(at, 1.0, Stick::from_axes(0.0, 1.0), 1.0);
        let placed = ahead_of(at, 1.0, REACH);
        assert!(close(walked.target[0], placed[0]), "{walked:?} {placed:?}");
        assert!(close(walked.target[2], placed[2]), "{walked:?} {placed:?}");
    }

    /// A junk yaw or distance must place the creature exactly on the player rather than at a NaN,
    /// which the proxy drain would then feed to `ForceSetPosition`.
    #[test]
    fn a_non_finite_input_places_the_creature_on_the_player_rather_than_nowhere() {
        let at = [1.0, 2.0, 3.0];
        assert_eq!(ahead_of(at, f32::NAN, 3.0), at);
        assert_eq!(ahead_of(at, 0.0, f32::INFINITY), at);
    }

    #[test]
    fn a_resting_stick_is_no_input_and_a_pushed_one_is() {
        assert_eq!(Stick::from_xinput(0, 0), None);
        assert_eq!(Stick::from_xinput(3000, 2000), None, "inside the deadzone");
        let pushed = Stick::from_xinput(0, 32767).expect("full forward is input");
        assert!(close(pushed.x, 0.0) && close(pushed.y, 1.0));
    }

    /// A per-axis deadzone would leave a cross-shaped dead region; a diagonal just past the corner
    /// would then read as a pure axis and the creature would walk 45 degrees off.
    #[test]
    fn the_deadzone_is_radial_not_per_axis() {
        // Each axis alone is under the threshold; together they are over it.
        let each = STICK_DEADZONE * 0.8;
        let stick = Stick::from_axes(each, each).expect("the diagonal is outside the deadzone");
        assert!(close(stick.x, stick.y), "and it stays diagonal");
        assert_eq!(Stick::from_axes(each, 0.0), None, "one axis alone is not");
    }

    /// A square-gated stick reads past 1.0 in the corners; the request must not get longer there.
    #[test]
    fn a_corner_deflection_is_clamped_to_unit_length() {
        let corner = Stick::from_axes(1.0, 1.0).expect("a corner is input");
        assert!(close(corner.x.hypot(corner.y), 1.0));
        let half = Stick::from_axes(0.0, 0.5).expect("half forward is input");
        assert!(close(half.y, 0.5), "a half push stays a half push");
    }

    /// NaN out of a bad read must not become a NaN move target.
    #[test]
    fn a_non_finite_axis_is_not_input() {
        assert_eq!(Stick::from_axes(f32::NAN, 0.0), None);
        assert_eq!(Stick::from_axes(0.0, f32::INFINITY), None);
    }

    /// The basis: yaw 0 faces `+Z`, a quarter turn faces `+X`, and Y is never touched -- writing a
    /// height into a walk target is how a ground creature is asked to fly.
    #[test]
    fn forward_is_plus_z_at_yaw_zero_and_plus_x_a_quarter_turn_later() {
        let at = [10.0, 5.0, -20.0];
        let forward = Stick::from_axes(0.0, 1.0);
        let north = drive(at, 0.0, forward, 1.0);
        assert!(north.moving());
        assert!(close(north.target[0], 10.0), "{:?}", north.target);
        assert!(close(north.target[1], 5.0), "height is never written");
        assert!(close(north.target[2], -20.0 + REACH), "{:?}", north.target);

        let east = drive(at, core::f32::consts::FRAC_PI_2, forward, 1.0);
        assert!(close(east.target[0], 10.0 + REACH), "{:?}", east.target);
        assert!(close(east.target[2], -20.0), "{:?}", east.target);
    }

    /// Right on the stick is right of the body, which is the other half of the basis and the half
    /// a sign error hides in.
    #[test]
    fn right_on_the_stick_is_ninety_degrees_clockwise_from_forward() {
        let at = [0.0, 0.0, 0.0];
        let right = drive(at, 0.0, Stick::from_axes(1.0, 0.0), 1.0);
        assert!(close(right.target[0], REACH), "{:?}", right.target);
        assert!(close(right.target[2], 0.0), "{:?}", right.target);
    }

    /// RELEASING THE STICK MUST STOP THE CREATURE. A stale target is a creature that keeps
    /// walking after the player let go, which is the single most alarming failure this can have.
    #[test]
    fn no_stick_asks_for_the_creatures_own_position_so_it_stops() {
        let at = [1.5, 2.5, 3.5];
        let idle = drive(at, 1.0, None, 1.0);
        assert!(!idle.moving());
        assert_eq!(idle.target, at, "arrived, by construction");
    }

    /// `speed_scale` lengthens the leash, and a junk one must not produce a junk target.
    #[test]
    fn speed_scale_scales_the_reach_and_nonsense_stops_the_creature() {
        let at = [0.0, 0.0, 0.0];
        let forward = Stick::from_axes(0.0, 1.0);
        let doubled = drive(at, 0.0, forward, 2.0);
        assert!(close(doubled.target[2], REACH * 2.0));
        for junk in [0.0, -1.0, f32::NAN] {
            let out = drive(at, 0.0, forward, junk);
            assert!(!out.moving(), "{junk}");
            assert_eq!(out.target, at, "{junk}");
        }
    }

    /// A creature whose yaw could not be read must not be sent somewhere arbitrary.
    #[test]
    fn a_non_finite_yaw_stops_the_creature_rather_than_guessing_a_heading() {
        let at = [4.0, 0.0, 4.0];
        let out = drive(at, f32::NAN, Stick::from_axes(0.0, 1.0), 1.0);
        assert!(!out.moving());
        assert_eq!(out.target, at);
    }

    /// The target must stay a finite point for every stick position around the circle -- this is
    /// the value that gets written into the engine's own path data.
    #[test]
    fn every_heading_produces_a_finite_target() {
        for step in 0..64u8 {
            let yaw = core::f32::consts::TAU * f32::from(step) / 64.0;
            let out = drive([100.0, -3.0, 7.0], yaw, Stick::from_axes(0.6, -0.8), 1.0);
            assert!(out.moving());
            assert!(out.target.iter().all(|v| v.is_finite()), "yaw {yaw}");
            // ...and always exactly one reach away, horizontally.
            let dx = out.target[0] - 100.0;
            let dz = out.target[2] - 7.0;
            assert!(close(dx.hypot(dz), REACH), "yaw {yaw}");
        }
    }

    /// THE GATE. A frame that is asking for movement must carry a non-zero `walkType`, because
    /// zero is the value the engine reads as "build no move vector at all" -- and a target written
    /// under a zero gait is exactly the bug this module was shipped with: perfect coordinates,
    /// consumed by a branch that never runs.
    #[test]
    fn a_moving_frame_never_carries_the_stop_gait() {
        let at = [0.0, 0.0, 0.0];
        for (x, y) in [(0.0, 1.0), (1.0, 0.0), (0.0, -1.0), (-0.7, 0.7), (0.3, 0.3)] {
            let Some(stick) = Stick::from_axes(x, y) else {
                continue;
            };
            let out = drive(at, 0.0, Some(stick), 1.0);
            assert!(out.moving(), "({x}, {y})");
            assert_ne!(out.walk_type, ai_ins::WALK_TYPE_STOP, "({x}, {y})");
        }
    }

    /// ...and the inverse: every frame that is NOT moving must carry the stop gait, or the body
    /// keeps walking toward its own position at a non-zero gait after the player let go.
    #[test]
    fn every_non_moving_frame_carries_the_stop_gait_and_the_bodys_own_position() {
        let at = [1.5, 2.5, 3.5];
        let stops = [
            drive(at, 1.0, None, 1.0),
            drive(at, f32::NAN, Stick::from_axes(0.0, 1.0), 1.0),
            drive(at, 0.0, Stick::from_axes(0.0, 1.0), -1.0),
            IntentWrite::stopped(at),
        ];
        for out in stops {
            assert!(!out.moving());
            assert_eq!(out.walk_type, ai_ins::WALK_TYPE_STOP);
            assert_eq!(out.target, at, "arrived, by construction");
        }
    }

    /// Half deflection or less walks, past it runs. Those are the only two moving values the
    /// engine's own `MoveTo` produces, so a third would be a value nothing in the game writes.
    #[test]
    fn a_gentle_push_walks_and_a_hard_one_runs() {
        let at = [0.0, 0.0, 0.0];
        let gait = |x: f32, y: f32| drive(at, 0.0, Stick::from_axes(x, y), 1.0).walk_type;
        assert_eq!(gait(0.0, 0.3), ai_ins::WALK_TYPE_WALK);
        assert_eq!(
            gait(0.0, RUN_DEFLECTION),
            ai_ins::WALK_TYPE_WALK,
            "at, not past"
        );
        assert_eq!(gait(0.0, 1.0), ai_ins::WALK_TYPE_RUN);
        // A corner is clamped to unit length, so it runs rather than reading as 1.41.
        assert_eq!(gait(1.0, 1.0), ai_ins::WALK_TYPE_RUN);
    }

    /// The turn target is the ONE value that steers, on every frame including the stopped ones --
    /// with the stop gait the engine's own branch holds the body's current facing instead, so the
    /// constant does not have to be conditional.
    #[test]
    fn every_frame_asks_the_body_to_face_where_it_was_told_to_walk() {
        let at = [0.0, 0.0, 0.0];
        for out in [
            drive(at, 0.0, Stick::from_axes(0.0, 1.0), 1.0),
            drive(at, 0.0, None, 1.0),
            IntentWrite::stopped(at),
        ] {
            assert_eq!(out.turn_target, ai_ins::TURN_TARGET_SELF);
        }
    }

    /// `turn_deadzone_deg` straightens a nearly-forward push into an exactly-forward one, so the
    /// heading the engine derives from the target is the heading the body already has and it walks
    /// straight instead of weaving.
    #[test]
    fn a_push_inside_the_turn_deadzone_asks_for_no_turn_at_all() {
        let at = [0.0, 0.0, 0.0];
        // Ten degrees off forward, with the deadzone at twenty.
        let off = 10.0_f32.to_radians();
        let nudged = Stick::from_axes(off.sin(), off.cos());
        let straightened = intent(at, 0.0, nudged, 1.0, 20.0);
        // Yaw 0 faces +Z, so "no turn asked for" is a target with no X component at all.
        assert!(close(straightened.target[0], 0.0), "{straightened:?}");
        assert!(close(straightened.target[2], REACH), "{straightened:?}");
        // ...and the same push outside the deadzone keeps its angle.
        let kept = intent(at, 0.0, nudged, 1.0, 5.0);
        assert!(kept.target[0] > 0.1, "{kept:?}");
    }

    /// Straightening must not change how hard the stick was pushed -- a walk that became a run
    /// (or the reverse) on the way through the deadzone would be a gait that depends on aim.
    #[test]
    fn the_turn_deadzone_changes_the_heading_and_not_the_gait_or_the_reach() {
        let at = [0.0, 0.0, 0.0];
        let off = 5.0_f32.to_radians();
        for magnitude in [0.3_f32, 0.5, 0.9, 1.0] {
            let stick = Stick::from_axes(off.sin() * magnitude, off.cos() * magnitude);
            let free = intent(at, 0.0, stick, 1.0, 0.0);
            let snapped = intent(at, 0.0, stick, 1.0, 20.0);
            assert_eq!(free.walk_type, snapped.walk_type, "{magnitude}");
            let reach = |w: IntentWrite| w.target[0].hypot(w.target[2]);
            assert!(close(reach(free), reach(snapped)), "{magnitude}");
        }
    }

    /// A junk deadzone must not straighten everything or panic; `NaN` comparisons are false, so
    /// the push simply keeps its angle.
    #[test]
    fn a_non_finite_turn_deadzone_leaves_the_push_alone() {
        let at = [0.0, 0.0, 0.0];
        let right = Stick::from_axes(1.0, 0.0);
        for junk in [f32::NAN, f32::INFINITY, -1.0] {
            let out = intent(at, 0.0, right, 1.0, junk);
            assert!(out.moving(), "{junk}");
            assert!(close(out.target[0], REACH), "{junk} {out:?}");
        }
    }
}
