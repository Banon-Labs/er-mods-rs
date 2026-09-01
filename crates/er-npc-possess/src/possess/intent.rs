//! WHERE THE POSSESSED CREATURE IS TOLD TO GO, and why it is told twice.
//!
//! # The race this module exists to lose safely
//!
//! With `[vt+0x48]` no-oped the AI never replans, but `[vt+0x50]` -- which we DO forward, because
//! it is the slot that actually moves the body -- still runs `AiIns::UpdateMovement` first. The
//! decompiled 1.16.2 body branches three ways, and which one a possessed creature lands in depends
//! on residual `pathData` state that cannot be determined statically:
//!
//! ```text
//! if HasFollowPathMoveTarget(pathData) && <a second path predicate> && !IsArrived()
//!         wantToMoveTo = own physics position          // "stop"
//! else if HasFollowPathMoveTarget(pathData) && !aiIns[0xe990] && !IsArrived()
//!         wantToMoveTo = pathData->target              // the path wins
//! else    wantToMoveTo unchanged                        // OUR value survives
//! ```
//!
//! So **neither field alone wins in every branch**. Writing only `wantToMoveTo` loses branch two;
//! writing only `pathData->target` loses branch three, where nothing copies it across. The
//! briefing that produced this crate preferred `pathData->target` on native-ownership grounds, and
//! that is the right instinct -- driving the path target is what the engine itself does -- but it
//! is not sufficient on its own.
//!
//! [`IntentWrite`] therefore emits BOTH, with the same value, every frame. That is strictly more
//! branches covered than either alone, and the two can never disagree because they are one number
//! written twice. Only branch one still escapes us, and no field write reaches it.
//!
//! # `turnTarget` is not a steering wheel
//!
//! `AiIns.turnTarget` is an `AiTargetPointType` -- a 4-byte enum naming WHICH known point to face,
//! not a yaw. `UpdateMovement` consumes it as `FUN_1402c9410(aiIns, aiIns->turnTarget)`. With goal
//! selection no-oped those points are not being refreshed, so writing it steers nothing. Facing is
//! left to the locomotion executor, which turns the body toward `wantToMoveTo` at the rate
//! `NpcParam+0x10 turnVellocity` gives it. There is deliberately no constant for `turnTarget`
//! in `layout::ai_ins`: the finding is recorded in that module's docs, because a constant would
//! be an invitation to use it.

// Pure math; stays ungated so its tests run on the host.
#![cfg_attr(not(windows), allow(dead_code))]

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
}

/// The pair of writes one frame of movement intent turns into.
///
/// A value rather than two calls, so the "both fields, same number" rule is a thing that exists
/// and can be tested rather than a discipline expected of the caller.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct IntentWrite {
    /// The physics-space point to walk to. Written to `AiIns.wantToMoveTo` AND to
    /// `AiIns.pathData->target`.
    pub(crate) target: [f32; 3],
    /// Whether this frame is asking for movement at all. `false` writes the creature's own
    /// position, which reads as "arrived" and stops it -- rather than leaving a stale target that
    /// would keep it walking after the stick was released.
    pub(crate) moving: bool,
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
#[must_use]
pub(crate) fn intent(
    position: [f32; 3],
    yaw: f32,
    stick: Option<Stick>,
    speed_scale: f32,
) -> IntentWrite {
    let Some(stick) = stick else {
        return IntentWrite {
            target: position,
            moving: false,
        };
    };
    if !yaw.is_finite() || !speed_scale.is_finite() || speed_scale <= 0.0 {
        return IntentWrite {
            target: position,
            moving: false,
        };
    }
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
        moving: true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Within a millimetre, which is far below anything the engine can act on.
    fn close(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-3
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
        let north = intent(at, 0.0, forward, 1.0);
        assert!(north.moving);
        assert!(close(north.target[0], 10.0), "{:?}", north.target);
        assert!(close(north.target[1], 5.0), "height is never written");
        assert!(close(north.target[2], -20.0 + REACH), "{:?}", north.target);

        let east = intent(at, core::f32::consts::FRAC_PI_2, forward, 1.0);
        assert!(close(east.target[0], 10.0 + REACH), "{:?}", east.target);
        assert!(close(east.target[2], -20.0), "{:?}", east.target);
    }

    /// Right on the stick is right of the body, which is the other half of the basis and the half
    /// a sign error hides in.
    #[test]
    fn right_on_the_stick_is_ninety_degrees_clockwise_from_forward() {
        let at = [0.0, 0.0, 0.0];
        let right = intent(at, 0.0, Stick::from_axes(1.0, 0.0), 1.0);
        assert!(close(right.target[0], REACH), "{:?}", right.target);
        assert!(close(right.target[2], 0.0), "{:?}", right.target);
    }

    /// RELEASING THE STICK MUST STOP THE CREATURE. A stale target is a creature that keeps
    /// walking after the player let go, which is the single most alarming failure this can have.
    #[test]
    fn no_stick_asks_for_the_creatures_own_position_so_it_stops() {
        let at = [1.5, 2.5, 3.5];
        let idle = intent(at, 1.0, None, 1.0);
        assert!(!idle.moving);
        assert_eq!(idle.target, at, "arrived, by construction");
    }

    /// `speed_scale` lengthens the leash, and a junk one must not produce a junk target.
    #[test]
    fn speed_scale_scales_the_reach_and_nonsense_stops_the_creature() {
        let at = [0.0, 0.0, 0.0];
        let forward = Stick::from_axes(0.0, 1.0);
        let doubled = intent(at, 0.0, forward, 2.0);
        assert!(close(doubled.target[2], REACH * 2.0));
        for junk in [0.0, -1.0, f32::NAN] {
            let out = intent(at, 0.0, forward, junk);
            assert!(!out.moving, "{junk}");
            assert_eq!(out.target, at, "{junk}");
        }
    }

    /// A creature whose yaw could not be read must not be sent somewhere arbitrary.
    #[test]
    fn a_non_finite_yaw_stops_the_creature_rather_than_guessing_a_heading() {
        let at = [4.0, 0.0, 4.0];
        let out = intent(at, f32::NAN, Stick::from_axes(0.0, 1.0), 1.0);
        assert!(!out.moving);
        assert_eq!(out.target, at);
    }

    /// The target must stay a finite point for every stick position around the circle -- this is
    /// the value that gets written into the engine's own path data.
    #[test]
    fn every_heading_produces_a_finite_target() {
        for step in 0..64u8 {
            let yaw = core::f32::consts::TAU * f32::from(step) / 64.0;
            let out = intent([100.0, -3.0, 7.0], yaw, Stick::from_axes(0.6, -0.8), 1.0);
            assert!(out.moving);
            assert!(out.target.iter().all(|v| v.is_finite()), "yaw {yaw}");
            // ...and always exactly one reach away, horizontally.
            let dx = out.target[0] - 100.0;
            let dz = out.target[2] - 7.0;
            assert!(close(dx.hypot(dz), REACH), "yaw {yaw}");
        }
    }
}
