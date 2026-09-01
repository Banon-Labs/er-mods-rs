//! THE POSSESSION ENGINE -- stack layer 2, the thing layer 1's seam was cut for.
//!
//! # What actually happens when the key is pressed
//!
//! 1. A `thunk::Thunk` is built: one page holding a 55-slot vtable whose stubs swap `rcx` to the
//!    creature's real `ComManipulator` and tail-jump, except `[vt+0x48]` (`UpdateAi`, no-oped --
//!    this is the feature) and `[vt+0x08]` (the scalar deleting destructor, which must never run
//!    with a swapped `rcx` or it frees the creature's real manipulator).
//! 2. That object's address goes into `ChrCtrl+0x3b0`, the dormant manipulator override slot.
//!    Three dispatchers check it before `ChrCtrl+0x18`, so the creature's think path dies and its
//!    execute path is untouched.
//! 3. The creature goes into `WorldChrManDbg+0xb8 camOverrideChrIns`. Camera and lock-on follow it
//!    for free; identity, damage and the save keep pointing at the real `PlayerIns`.
//! 4. The player's own body is neutered -- invincible, alpha 0, silent, `debugFlags |= noAttack`
//!    -- and co-located with the creature every frame through the engine's own proxy drain.
//! 5. Every frame after that, movement intent is written into the creature's `AiIns`.
//!
//! # The three things this layer does NOT do, named so nobody has to go looking
//!
//! * **Attacks.** The `W_Event` vs `W_Attack` animation-name-prefix question is unresolved, and a
//!   wrong prefix silently no-ops rather than failing. `driver`'s `possess` leaves the
//!   seam and the log says so.
//! * **Untargetable.** `IsLockOnDisabled` reads `ChrCtrl+0xc8 -> +0x8 -> +0x10 actionFlags` bit 3,
//!   which lives in the SpEffect-accumulated modifier block and is re-derived every frame. A raw
//!   write is undone before it is read; the supported route is a SpEffect row, which this layer
//!   does not have. The player's body is invisible, silent and invincible, so a hostile that locks
//!   onto it is a cosmetic oddity rather than a hazard.
//! * **Save suppression.** [`teardown::Step::LiftSaveSuppression`] exists and runs last, and it
//!   it lifts nothing, because nothing is suppressed: co-location means the real `PlayerIns` is
//!   genuinely standing where the creature is, so `UpdateSafePosition` writing the save's respawn
//!   fields from that position is CORRECT rather than something to hold off. The step is kept
//!   because the ordering constraint is real the moment anything ever does suppress.

// The offset table, the thunk emitter, the teardown ordering and the movement math are pure and
// stay ungated, so `cargo test` proves them on the host with no game running. `game` and the
// engine that drives them touch live memory through the windows-only `eldenring` bindings.
pub(crate) mod intent;
pub(crate) mod layout;
pub(crate) mod teardown;
pub(crate) mod thunk;

#[cfg(windows)]
pub(crate) mod game;

#[cfg(windows)]
mod driver;

#[cfg(windows)]
pub(crate) use driver::NpcPossessionEngine;

/// THE 16-BYTE ALIGNMENT, checked here rather than only where it is used.
///
/// Every position and rotation this engine hands the game goes into a `FloatVector4`, and the
/// engine's own proxy drain loads it with **`MOVAPS`** (`CSChrPhysicsModule::ForceSetPosition`),
/// which `#GP`s -- an instant, unexplained crash -- on an address that is not 16-aligned. The type
/// is `er-invasion-warp-core`'s, and it carries its own test there; this one exists because the
/// requirement belongs to the CALLER as much as to the type. A future edit that swapped in a
/// local `#[repr(C)] struct FloatVector4` would compile, run, look right in review, and take the
/// game down the first time a possession moved anybody.
///
/// The `game` module that performs those writes is windows-only, so this is the only place the
/// invariant can be asserted on the host.
#[cfg(test)]
mod tests {
    use er_invasion_warp_core::warp::FloatVector4;

    #[test]
    fn the_vector_handed_to_the_engine_is_sixteen_byte_aligned() {
        assert_eq!(core::mem::align_of::<FloatVector4>(), 16);
        assert_eq!(core::mem::size_of::<FloatVector4>(), 16);
        // ...and a stack instance really is aligned, which is what MOVAPS actually reads.
        let vector = FloatVector4::new(1.0, 2.0, 3.0, 1.0);
        assert_eq!(core::ptr::from_ref(&vector) as usize % 16, 0);
    }

    /// The engine writes `ChrCtrl+0x100` and `+0x110`, and both must stay 16-aligned INSIDE a
    /// 16-aligned `ChrCtrl` -- an offset that is not a multiple of 16 would be unaligned however
    /// well the allocation itself is aligned.
    #[test]
    fn both_proxy_request_fields_sit_on_sixteen_byte_boundaries() {
        for offset in [
            super::layout::chr_ctrl::RAGDOLL_POSITION,
            super::layout::chr_ctrl::RAGDOLL_ROTATION,
        ] {
            assert_eq!(offset % 16, 0, "{offset:#x}");
        }
    }
}
