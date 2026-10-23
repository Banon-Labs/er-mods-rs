//! Every game STRUCT offset the camera layer touches, and the build gate over them.
//!
//! Same shape and same reasons as [`crate::possess::layout`]: a table of constants rather than
//! typed field access, because the answer for a build nobody has measured has to be **`None`**
//! and a struct field cannot give that answer.
//!
//! # What was measured, and how
//!
//! Every offset below was read out of the 2.7.2.0 image (`eldenring-deobf-1.17.2.bin`); the same
//! sites were byte-proven on 1.16.2 and 1.17 before that. The evidence, in the order a reader
//! would want to re-run it:
//!
//! * **`WorldChrMan+0x1ecf0 chrCam`** -- the 1.16.2 named dump types the field (`FUN_1404a6c30`
//!   there, 92 bytes: `UpdateRecursive`, then `GLOBAL_WorldChrMan`, then `[+chrCam]`, then a 4x4
//!   matrix copy). The same function is 2.7.2.0 `0x1404a7270`, loading `[rax+0x1ecf0]` at
//!   `0x1404a728e` off `GLOBAL_WorldChrMan` at `0x143d6d018`; see [`world_chr_man::CHR_CAM`].
//! * **`ChrCam+0x60 chrExFollowCam`** -- `CS::ChrCam::Update` is `0x1403b11d0`, and four sites in
//!   it (`0x1403b12b4`, `0x1403b1359`, `0x1403b137d`, `0x1403b145b`) do `MOV RCX,[RDI+0x60]`
//!   ahead of `ChrExFollowCam::Update` (`0x1403b5ad0`). The crate's `ChrCam` models the same
//!   field; the `const` assertion in [`crate::camera::game`] pins the two together through
//!   `size_of::<CSPersCam>()`.
//! * **`ChrExFollowCam+0x468`** -- see [`chr_ex_follow_cam::LOCK_CAM_PARAM_OVERRIDE`].
//! * **`ChrExFollowCam+0x460`** -- written by `ApplyZoomLerp` at `0x1403b76da`
//!   (`mov [rbx+0x460],edx`).
//! * **`ChrExFollowCam+0x150/+0x154 anglesEuler`** -- see
//!   [`chr_ex_follow_cam::ANGLES_EULER_YAW`]; `ChrExFollowCam::Update` reads them at
//!   `0x1403b62f8` / `0x1403b62e4` (`movss xmm1,[rsi+0x150]` / `movss xmm1,[rsi+0x154]`).
//! * **`CSChrPhysicsModule+0x340/+0x344`** -- `ChrIns::GetPhysicsHitHeight` is
//!   `[ChrIns+0x190] -> [+0x68] -> MOVSS XMM0,[RCX+0x340]; RET` in the 1.16.2 named dump; on
//!   2.7.2.0 the chain is `0x1403efe50 -> 0x14045e670`, and its neighbour
//!   `0x1403efe60 -> 0x14045e680` reads `0x344`, the radius.
//!
//! This module still gates: a build other than 2.7.2.0 gets `None`, the camera is left alone, and
//! the derived file says `unmeasured-build` rather than the mod writing a float into whatever now
//! lives at `+0x468`.

// Pure arithmetic; ungated so its tests run on the host.
#![cfg_attr(not(windows), allow(dead_code))]

use er_game_base::game_build::{FileVersion, SUPPORTED_FILE_VERSION};

/// `WorldChrManImp`.
pub(crate) mod world_chr_man {
    /// `WorldChrManImp.chrCam` on 2.7.2.0.
    ///
    /// 2.7.2.0 inserted two qwords at `+0x1e5f0` (the new spEffect sync queue and its partner
    /// object), so every member from there on is `+0x10` past where 1.17.1 had it. Measured: the
    /// `WorldChrManImp` allocation in `_Common_Initialize` is `mov ecx,0x1f3f0` (2.7.2.0
    /// `0x140aef292`; 1.17.1 had `0x1f3e0`), and the 92-byte chrCam reader this module's doc cites
    /// loads `[rax+0x1ecf0]` at `0x1404a728e`. The fromsoftware-rs `WorldChrMan.chr_cam` field
    /// still sits at the 1.17.1 offset, so the typed field must not be read.
    pub(crate) const CHR_CAM: usize = 0x1ecf0;
}

/// `ChrCam`, the object `WorldChrMan.chrCam` points at.
pub(crate) mod chr_cam {
    /// `ChrCam.chrExFollowCam`. The crate spells this field privately, so the compile-time
    /// cross-check goes through `size_of::<CSPersCam>()` instead -- `ChrCam` opens with its
    /// `CSPersCam` superclass and this pointer is the very next field.
    pub(crate) const EX_FOLLOW_CAM: usize = 0x60;
}

/// `ChrExFollowCam` -- the third-person follow camera, and the only thing this layer writes.
pub(crate) mod chr_ex_follow_cam {
    /// `ChrExFollowCam+0x468` -- The lever, and the reason this layer needs no detour.
    ///
    /// `CS::ChrExFollowCam::ApplyZoomLerp` (2.7.2.0 `0x1403b7570`) runs every frame out of
    /// `ChrExFollowCam::Update` and opens its param-id resolution by loading this field, before
    /// the branch that picks lock-on or normal framing. It is first in both chains:
    ///
    /// * normal: `+0x468` -> `GameMan+0x40` -> `GameMan+0x48` -> `GameMan+0x4c` -> `+0x464` -> 0
    /// * lock-on: `+0x468` -> `GameMan+0x44` -> `GameMan+0x54` -> `+0x464` -> 0
    ///
    /// **Nothing in the game ever writes it.** Across every `.pdata` function starting in the
    /// camera code range (`0x1403b0000`-`0x1403c0000`) of the 2.7.2.0 image there is exactly one
    /// instruction with displacement `0x468`, the `MOV EAX,[RBX+0x468]` load at `0x1403b7668`;
    /// image-wide (measured on 1.17) there were only eight 32-bit stores at that displacement and
    /// every one is in unrelated code far outside the camera module (several are `[RBP+0x468]`
    /// stack frames). The constructor sets it with a single qword store covering `+0x464|+0x468`
    /// -- `MOV qword [RDI+0x464],-1` at 2.7.2.0 `0x1403b3b93` -- and never touches it again.
    ///
    /// `+0x464` is not free and must not be used instead: `FUN_1403b1140` writes it from the map
    /// region (`MOV [RCX+0x464],EDX` at 2.7.2.0 `0x1403b5950`).
    pub(crate) const LOCK_CAM_PARAM_OVERRIDE: usize = 0x468;
    /// `ChrExFollowCam+0x460` -- the id `ApplyZoomLerp` actually resolved last frame, mirrored out
    /// for free (`MOV [RBX+0x460],EDX` at 2.7.2.0 `0x1403b76da`).
    ///
    /// Read once at possession start to learn which row the camera was using a frame ago, so the
    /// fields the size law does not decide can be copied from it rather than from a guess.
    pub(crate) const RESOLVED_LOCK_CAM_PARAM: usize = 0x460;

    /// `ChrExFollowCam+0x150 anglesEuler` -- the camera's own Euler angles. `x` is the pitch.
    ///
    /// Named here only so [`ANGLES_EULER_YAW`] can say what it is one float past. Nothing reads
    /// the pitch: the body turns about Y only.
    pub(crate) const ANGLES_EULER: usize = 0x150;
    /// `ChrExFollowCam+0x154 anglesEuler.y` -- Where the player is looking, as one float.
    ///
    /// # What the number is, exactly
    ///
    /// `ChrExFollowCam::Update` closes by deriving the camera's Euler angles from the vector
    /// between the point it is aimed at and the smoothed subject anchor:
    ///
    /// ```text
    /// delta = targetCoordinates(+0xe0) - pivot         ; MOVAPS/SUBPS, 1.16.2 0x1403b62c9
    /// pitch = angleFromXZPlane(delta, anglesEuler.x)   ; +0x150 goes in as the fallback
    /// yaw   = angleOnXZPlane  (delta, anglesEuler.y)   ; +0x154 goes in as the fallback
    /// anglesEuler = (pitch, yaw, pitch, yaw)           ; three UNPCKLPS, then MOVAPS to +0x150
    /// ```
    ///
    /// and `angleOnXZPlane` (1.16.2 `0x1403b0c20`) is `atan2f(v.x, v.z)`, returning the passed-in
    /// previous angle unchanged when the delta has no horizontal part. So this field is
    /// **`atan2(look.x, look.z)`** and nothing else.
    ///
    /// # Why the angle and not the two coordinate fields
    ///
    /// Because `targetCoordinates` is differenced against the smoothed pivot above, not against
    /// `cameraCoordinates(+0x110)`. Subtracting those two points would produce a
    /// plausible-looking direction that the engine never actually computes. This is the engine's
    /// own answer, in one read.
    ///
    /// # Sign
    ///
    /// A character's yaw is `atan2(-d.x, -d.z)` for the direction `d` it faces -- two independent
    /// proofs in [`crate::possess::intent`] -- so the body heading for this look direction is this
    /// field plus PI. [`crate::possess::intent::aim`] is the one place that conversion happens.
    ///
    /// Byte-proven: the 73-byte window carrying `+0xe0`, `+0x150` and `+0x154` matched uniquely in
    /// 1.16.2 and 1.17, and on 2.7.2.0 it opens with `movaps xmm0,[rsi+0xe0]` at `0x1403b62d9`.
    pub(crate) const ANGLES_EULER_YAW: usize = ANGLES_EULER + core::mem::size_of::<f32>();
}

/// `CSChrPhysicsModule` -- reached through `ChrIns+0x190 -> +0x68`, the same chain
/// [`crate::possess::layout::modules`] already names.
pub(crate) mod chr_physics_module {
    /// `hitHeight`, metres. What `CS::ChrIns::GetPhysicsHitHeight` returns, populated from
    /// `NpcParam.hitHeight` by `CSChrPhysicsModule::InitForEnemy`.
    ///
    /// This is the size scalar the whole layer rests on, and it is the only one available: ELDEN
    /// RING has no chr-scale param at all, and the FLVER bounding box is build-time-only and
    /// degenerate (`FLT_MAX`) on eleven chrs including `c0000`.
    pub(crate) const HIT_HEIGHT: usize = 0x340;
    /// `hitRadius`, metres -- the horizontal half-extent, i.e. the distance inside which the
    /// camera would be inside the model.
    pub(crate) const HIT_RADIUS: usize = 0x344;
}

/// The offsets for the running build, or `None` on one nobody has measured.
///
/// A struct rather than a bool: when a field moves on a later build, the difference belongs here
/// and every caller picks it up without changing.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Offsets {
    pub(crate) chr_cam: usize,
    pub(crate) ex_follow_cam: usize,
    pub(crate) lock_cam_param_override: usize,
    pub(crate) resolved_lock_cam_param: usize,
    pub(crate) hit_height: usize,
    pub(crate) hit_radius: usize,
    pub(crate) angles_euler_yaw: usize,
}

/// The measured offsets, or `None`.
#[must_use]
pub(crate) fn offsets(version: Option<FileVersion>) -> Option<Offsets> {
    if version? != SUPPORTED_FILE_VERSION {
        return None;
    }
    Some(Offsets {
        chr_cam: world_chr_man::CHR_CAM,
        ex_follow_cam: chr_cam::EX_FOLLOW_CAM,
        lock_cam_param_override: chr_ex_follow_cam::LOCK_CAM_PARAM_OVERRIDE,
        resolved_lock_cam_param: chr_ex_follow_cam::RESOLVED_LOCK_CAM_PARAM,
        hit_height: chr_physics_module::HIT_HEIGHT,
        hit_radius: chr_physics_module::HIT_RADIUS,
        angles_euler_yaw: chr_ex_follow_cam::ANGLES_EULER_YAW,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_measured_build_answers_and_nothing_else_does() {
        let measured = offsets(Some(SUPPORTED_FILE_VERSION)).expect("2.7.2.0 is measured");
        assert_eq!(measured.chr_cam, 0x1ecf0);
        for other in [
            FileVersion {
                major: 2,
                minor: 7,
                build: 1,
                revision: 0,
            },
            FileVersion {
                major: 2,
                minor: 8,
                build: 0,
                revision: 0,
            },
        ] {
            assert_eq!(
                offsets(Some(other)),
                None,
                "a build nobody measured: {other}"
            );
        }
        // The host, where there is no game image at all.
        assert_eq!(offsets(None), None);
    }

    /// The override slot and the mirror are adjacent `i32`s and must not be confused: writing the
    /// mirror does nothing at all, because `ApplyZoomLerp` overwrites it every frame.
    #[test]
    fn the_override_and_the_mirror_are_distinct_adjacent_words() {
        assert_eq!(
            chr_ex_follow_cam::LOCK_CAM_PARAM_OVERRIDE - chr_ex_follow_cam::RESOLVED_LOCK_CAM_PARAM,
            core::mem::size_of::<i32>() * 2,
            "+0x460 mirror, +0x464 map-region id, +0x468 our override"
        );
    }

    /// The camera yaw is the second float of the Euler vector; the one beside it is the pitch.
    ///
    /// Reading `+0x150` instead would aim the body with the camera's elevation, which shows up as
    /// "the creature spins when I look up" rather than as anything that looks like an offset bug.
    #[test]
    fn the_camera_yaw_is_the_y_component_and_the_pitch_is_the_x() {
        assert!(
            chr_ex_follow_cam::ANGLES_EULER.is_multiple_of(16),
            "the whole vector is stored with one MOVAPS, so it is 16-aligned"
        );
        assert_eq!(
            chr_ex_follow_cam::ANGLES_EULER_YAW,
            0x154,
            "the byte-proven displacement, checked against the derivation rather than repeated \
             by it: +0x150 pitch, +0x154 yaw, because the UNPCKLPS interleave puts the yaw second"
        );
    }

    /// Height and radius are adjacent floats in the physics module, in that order.
    #[test]
    fn the_hit_extents_are_adjacent_floats() {
        assert_eq!(
            chr_physics_module::HIT_RADIUS - chr_physics_module::HIT_HEIGHT,
            core::mem::size_of::<f32>()
        );
    }
}
