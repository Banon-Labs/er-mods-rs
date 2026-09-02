//! EVERY GAME STRUCT OFFSET THE ENGINE TOUCHES, IN ONE PLACE, AND NOTHING ELSE.
//!
//! # Why a module of constants rather than the `eldenring` crate's structs
//!
//! `fromsoftware-rs` models most of these fields already, and the engine could reach half of them
//! through a typed reference. It deliberately does not, for one reason that is worth the
//! duplication: **one of these offsets MOVED between the two builds this repo supports** --
//! `ChrIns.debugFlags` is `+0x530` on 1.16.2 and `+0x538` on 1.17 (byte-proven: the same three
//! sites test `[reg+0x538]` in 1.17). A typed field access compiles to one offset and cannot say
//! which build it is for, so on the wrong build it writes into `stamina_recovery` and reports
//! success. [`debug_flags_offset`] answers `None` on a build nobody has measured, which is the
//! only honest third answer and the one a struct field cannot give.
//!
//! Having decided that for one field, the rest follow it: an offset table that is HALF typed
//! fields and half constants is a table where nobody can tell which half was checked.
//!
//! # What checks them
//!
//! * On Windows, [`crate::possess::game`] carries `const` assertions comparing each constant here
//!   against `core::mem::offset_of!` on the `eldenring` struct that models the same field. That
//!   is a compile-time cross-check of two independently derived layouts, and it costs nothing at
//!   runtime. Only the crate's `pub` fields can be asserted; the ones it spells `unkNNN` are
//!   marked RE-ONLY below.
//! * On the host, the tests at the foot of this file check the invariants that are arithmetic --
//!   the build gate, the flag bits, and that the override slot is nobody else's field.
//!
//! Every value is from the settled reverse engineering recorded in `bd`
//! (`chrmanipulator-vtable-55-slots-thunk-design-2026-09-01`,
//! `possess-body-neuter-levers-debugflags-invincible-2026-09-01`,
//! `per-frame-chrins-move-without-fall-damage-2026-09-01`,
//! `possess-release-drop-in-place-physics-path-2026-09-01`) unless a comment says otherwise.

// This module is pure arithmetic and stays ungated so its tests run on the host.
#![cfg_attr(not(windows), allow(dead_code))]

use er_game_base::game_build::{FileVersion, SUPPORTED_FILE_VERSION};

/// ELDEN RING 1.17, PE `FileVersion` 2.7.0.0 -- the build installed on this machine since
/// 2026-08-27. [`SUPPORTED_FILE_VERSION`] is its 1.16.2 counterpart, 2.6.2.0.
pub(crate) const FILE_VERSION_1170: FileVersion = FileVersion {
    major: 2,
    minor: 7,
    build: 0,
    revision: 0,
};

/// `ChrIns` field offsets.
pub(crate) mod chr_ins {
    /// `ChrIns.chrCtrl`. Cross-checked against `offset_of!(ChrIns, chr_ctrl)`.
    pub(crate) const CHR_CTRL: usize = 0x58;
    /// `ChrIns.modules` -- the `ChrInsModuleContainer`. Cross-checked.
    pub(crate) const MODULES: usize = 0x190;
    /// `ChrIns.chrFlags1c5`. Bit `0x10` is INVINCIBILITY and nothing else -- not visibility, not
    /// targeting (exhaustive byte scan: 2 readers, 2 setters, 3 clearers). Cross-checked.
    ///
    /// Writing it directly is exactly what `CS::ChrIns::EnableInvincible` does; its whole body is
    /// `chrFlags1c5 = (chrFlags1c5 & 0xef) | (state << 4)`. See [`INVINCIBLE`].
    pub(crate) const CHR_FLAGS_1C5: usize = 0x1c5;
    /// The invincibility bit inside [`CHR_FLAGS_1C5`].
    ///
    /// **IT IS ALSO THE GRAB GATE, and that is settled rather than suspected.**
    /// `ChrIns::IsImmuneToAttack` (ChrIns vtable `+0x1D8`; 1.16.2 `0x1403f3b90`, 1.17
    /// `0x1403f3dc0` -- both read `+0x1c5 & 0x10` verbatim) answers "immune" on this bit unless
    /// the landed `AtkParam` row sets `isDisableNoDamage`. Every route into a throw is behind it:
    /// `ApplyDamage` is the only caller of `InitThrow`, `HitChr` is the only caller of
    /// `ApplyDamage`, and the two hit-resolution routines that reach `HitChr` with a real
    /// attacker (`FUN_14044a910` melee, `FUN_14038b2f0` bullet; 1.17 `0x14044ae70` /
    /// `0x14038b300`) both call it only after `FUN_1404443e0` -> `IsImmuneToAttack` has cleared
    /// the victim. So while this bit is set the possessing player's own body -- which
    /// `ThrowParam.DefChrId` makes the only legal victim for 189 of the game's 190 creature rows
    /// -- refuses the grab, and the initiator plays as a swing that misses. There is no
    /// per-attacker exemption to find: the predicate's only attacker-dependent term
    /// (`actionModifiersFlags` bit 5) makes it MORE immune to a non-player attacker, never less.
    pub(crate) const INVINCIBLE: u8 = 0x10;
    /// `ChrIns.tintAlphaMultiplier`. 0.0 is invisible, 1.0 is opaque. Cross-checked.
    pub(crate) const TINT_ALPHA_MULTIPLIER: usize = 0x240;
    /// `ChrIns.tintAlphaMultiplierModifier` -- a per-frame DECAY toward 0 (negative) or 1
    /// (positive). `SetFadeInOut` writes this, which is why calling it FADES rather than holds:
    /// we write both fields directly and keep the modifier at zero so the alpha stays put.
    /// Cross-checked.
    pub(crate) const TINT_ALPHA_MULTIPLIER_MODIFIER: usize = 0x244;

    /// `ChrIns.chrFlags1c8` -- the byte that carries the SOUND MUTE. Cross-checked, and unchanged
    /// on 1.17 (it is below the `+0x3b8` insertion point).
    pub(crate) const CHR_FLAGS_1C8: usize = 0x1c8;
    /// `chrFlags1c8` bit `0x40`: SET means SILENT.
    ///
    /// All four TAE sound handlers (event ids 129/132/133/134) open with
    /// `if (FUN_1403f4680(chrIns)) return;`, and that gate's first instruction is
    /// `TEST byte [rcx+0x1c8],0x40` -- set means `MOV AL,1; RET`, i.e. drop the sound.
    ///
    /// The POLARITY is not inferred from the name: the engine's own writer is a distance
    /// hysteresis (`FUN_140401d80`) that SETS this bit once a character is further from the player
    /// than `NpcParam.enableSoundObjDist`, or while it is ragdolling. Distant NPCs going quiet is
    /// shipped behaviour using this exact bit.
    ///
    /// It also does not fight us per frame, which is what makes it usable: that whole recompute
    /// sits inside `if (!IsPlayerIns(chrIns))`, so nothing recalculates it on the player's body.
    /// The only writers that can reach us are four CLEARS at (re)spawn/teleport -- in
    /// `PlayAnimation`, `Respawn`, `FUN_1403fc9a0` and `SummonHorse` -- which is why the
    /// per-frame block re-asserts it rather than setting it once.
    pub(crate) const MUTE_SOUND: u8 = 0x40;
    /// `chrFlags1c8` bit `0x80`: a ONE-SHOT that stops sounds already playing.
    ///
    /// `PostPhysicsSafe` sees `flags1c8 >= 0x80`, clears the bit, and walks
    /// `CSChrDataModule+0x98` stopping every sound in flight on that character. The engine writes
    /// `|= 0xC0` for exactly this pairing, and so do we: [`MUTE_SOUND`] silences what has not
    /// started, this silences what already has.
    pub(crate) const STOP_PLAYING_SOUNDS: u8 = 0x80;

    /// `ChrIns.debugFlags` on ELDEN RING 1.16.2.
    pub(crate) const DEBUG_FLAGS_1162: usize = 0x530;
    /// `ChrIns.debugFlags` on ELDEN RING 1.17. IT MOVED; see the module docs.
    pub(crate) const DEBUG_FLAGS_1170: usize = 0x538;
    /// `debugFlags` bit `0x10`: the character issues no attack requests.
    ///
    /// In `PadManipulator::Tick` this is tested BEFORE `IsMainPlayerIns`, so it works on the main
    /// player -- which is the whole reason it is the lever for neutering the possessing player's
    /// own body. It routes to `padManip+0x1e4` and skips the entire 16-entry `ChrActionType`
    /// request loop: R1/R2/L1/L2, guard, the action button, USE_ITEM and the quick slots.
    ///
    /// UNPROVEN and worth knowing: whether roll/jump/sprint are inside those 16 entries. Only
    /// index 4 (action button) and index 7 (USE_ITEM) are named, so this may cost rolls too.
    pub(crate) const DEBUG_FLAG_NO_ATTACK: u32 = 0x10;
    /// `debugFlags` bit `0x20`: the move vector is forced to zero.
    ///
    /// DELIBERATELY LEFT CLEAR by the possession engine. The player's body is co-located with the
    /// creature every frame by writing the proxy request directly, and freezing its move vector
    /// buys nothing while making the body fight that write.
    pub(crate) const DEBUG_FLAG_NO_MOVE: u32 = 0x20;
}

/// `ChrCtrl` field offsets.
pub(crate) mod chr_ctrl {
    /// `ChrCtrl.owner` -- back to the `ChrIns`. Cross-checked.
    pub(crate) const OWNER: usize = 0x10;
    /// `ChrCtrl.manipulator` -- the REAL manipulator. Never written by this engine: the AI-side
    /// lookups (`ChrIns::GetAiInsFromManipulator`, `EnemyIns::GetChrManipulator`) read this and
    /// must keep seeing the creature's own `ComManipulator`, which is what lets us write AI intent
    /// while the tick path sees our thunk. Cross-checked.
    pub(crate) const MANIPULATOR: usize = 0x18;
    /// `ChrCtrl.modifier` -- `ChrCtrlModifier`. Cross-checked.
    pub(crate) const MODIFIER: usize = 0xc8;
    /// `ChrCtrl.chrProxyFlags`. Cross-checked.
    pub(crate) const CHR_PROXY_FLAGS: usize = 0xfc;
    /// `chrProxyFlags` bit 0: drain `ragdollPosition` through `ForceSetPosition`.
    pub(crate) const CHR_PROXY_FLAG_POSITION: u32 = 1;
    /// `chrProxyFlags` bit 1: drain `ragdollRotation` through `SetOrientation`.
    pub(crate) const CHR_PROXY_FLAG_ROTATION: u32 = 2;
    /// `ChrCtrl.ragdollPosition`, a `FloatVector4`. RE-ONLY (`unk100` in the crate).
    pub(crate) const RAGDOLL_POSITION: usize = 0x100;
    /// `ChrCtrl.ragdollRotation` -- **EULER RADIANS `{0, yaw, 0, 0}`, NOT a quaternion**. The
    /// drain feeds it to `CSChrPhysicsModule::SetOrientation`, which feeds `EulerToQuat`.
    /// RE-ONLY (`unk110` in the crate).
    pub(crate) const RAGDOLL_ROTATION: usize = 0x110;
    /// `ChrCtrl+0x3b0` -- THE MANIPULATOR OVERRIDE SLOT, and the whole reason this mod is
    /// possible. RE-ONLY (`unk3b0` in the crate).
    ///
    /// Nothing in retail ever writes or frees it: a byte scan of every `mov [r+0x3b0],r64` form
    /// finds exactly ONE site image-wide, the `ChrCtrl` constructor's zero-init, and no reader
    /// anywhere calls `[manip+8]` (the destructor) on it. So we own the object outright.
    ///
    /// TEARDOWN HAZARD: `ChrCtrl::Unref` compares this against zero and **DLPanics** when it is
    /// non-null. It must be cleared before the character is torn down -- see
    /// [`crate::possess::teardown`].
    pub(crate) const MANIPULATOR_OVERRIDE: usize = 0x3b0;
}

/// `ChrCtrlModifier` / `ChrCtrlModifierData`.
pub(crate) mod chr_ctrl_modifier {
    /// `ChrCtrlModifier.data`.
    pub(crate) const DATA: usize = 0x8;
    /// `ChrCtrlModifierData+0x1c`, Ghidra's `1cFlags`.
    ///
    /// Bit 0 is **isDead**, read straight out of `CS::ChrIns::IsDead`, whose entire body is
    /// `return chrCtrl->ctrlModifier->ChrCtrlModifierData._1cFlags & 1`. A `bd` memory
    /// (`possess-body-neuter-levers-debugflags-invincible-2026-09-01`) calls bit 0 of this field
    /// "the invisible-state flag"; that is wrong, and the decompiled `IsDead` is the correction.
    pub(crate) const FLAGS_1C: usize = 0x1c;
    /// The death bit inside [`FLAGS_1C`].
    pub(crate) const FLAG_IS_DEAD: u32 = 1;
}

/// `ChrInsModuleContainer` slots, by index times eight.
pub(crate) mod modules {
    /// `CSChrDataModule`. Cross-checked.
    pub(crate) const DATA: usize = 0x00;
    /// `CSChrTimeActModule` -- the animation queue, i.e. what is playing right now.
    /// Cross-checked.
    pub(crate) const TIME_ACT: usize = 0x18;
    /// `CSChrBehaviorModule` -- carries `rootMotion`, which is how the watchdog tells a slow
    /// wind-up from a creature that has genuinely stopped. Cross-checked.
    pub(crate) const BEHAVIOR: usize = 0x28;
    /// `CSChrEventModule` -- the animation REQUEST slot. Cross-checked.
    pub(crate) const EVENT: usize = 0x58;
    /// `CSChrPhysicsModule`. Cross-checked.
    pub(crate) const PHYSICS: usize = 0x68;
}

/// `CSChrEventModule` -- how an attack is fired, and why it costs no game address.
///
/// `CS::CSChrEventModule::RequestAnimation` (1.16.2 `0x14043aa30`) has a two-line body: it calls
/// `SetRendererVisibility(chr, 5)` and then writes `requestAnimationId`. The visibility call is a
/// min-latch into `ChrIns+0xBC` that the `ChrIns` update RESETS TO -2 every frame, so it has no
/// lasting effect and nothing to undo. Which leaves a single `int` store -- so **writing this
/// field IS calling the function**, and the moveset layer keeps the crate's no-game-addresses
/// property intact.
///
/// `CSChrEventModule::Update` (1.16.2 `0x14043a580`) consumes it once per frame and resets it to
/// -1, routing through `FUN_140c14400`, which formats `DLString::FormatW(L"%s%04d", L"W_Event",
/// animId)` and resolves it against the same behaviour world `PlayAnimationByBehaviorName` uses.
/// So the clip carries its TimeAct binding and the hitbox, VFX, sound and root motion all come
/// along -- none of which this crate has to reimplement.
///
/// FOUR GATES sit in front of it, all of which a possessed creature ordinarily passes:
/// `!ChrIns::IsDead`, `actionFlag->actionAnimationFlags` bit 12 clear, `!CSChrThrowModule::IsInTrow`,
/// and `field_0x70 == 0`. A request that fails them is dropped silently; nothing here can observe
/// that, which is part of why the watchdog exists.
pub(crate) mod chr_event_module {
    /// `requestAnimationId`, `i32`. -1 means "nothing pending". Cross-checked.
    pub(crate) const REQUEST_ANIMATION_ID: usize = 0x18;
}

/// `CSChrTimeActModule` -- the ten-entry ring buffer of animations.
pub(crate) mod chr_time_act_module {
    /// `animQueue`, ten `CSChrTimeActModuleAnim`. Cross-checked.
    pub(crate) const ANIM_QUEUE: usize = 0x20;
    /// Size of one queue entry: `animId`, `playTime`, `playTime2`, `animLength`.
    pub(crate) const ANIM_STRIDE: usize = 0x10;
    /// Entries in the ring.
    pub(crate) const ANIM_QUEUE_LEN: u32 = 10;
    /// `readIdx`, `u32` -- the index of the animation last played or updated, i.e. the current
    /// one. Cross-checked.
    pub(crate) const READ_IDX: usize = 0xc4;
}

/// `CSChrBehaviorModule`.
///
/// # Reaching the `hkbCharacter`, without calling anything
///
/// `PlayAnimationByBehaviorName` wants a pointer to a slot holding the creature's `hkbCharacter`.
/// `CSChrEventModule::Update` gets one by calling `FUN_14041aef0(behaviorModule, &slot)`, which
/// calls `FUN_140c07a40(behaviorModule->field_0x10, &slot)`, whose entire body is
/// `slot = *(behaviorModule->field_0x10 + 0x30)`. Two loads and a null check -- so this crate does
/// the two loads and skips both calls, and the only address the moveset layer resolves is the one
/// it genuinely cannot avoid.
pub(crate) mod chr_behavior_module {
    /// The `hkbCharacter` owner this module hangs off. RE-ONLY (`unk10` in the crate).
    pub(crate) const HKB_OWNER: usize = 0x10;
    /// ...and the `hkbCharacter` itself, inside that. RE-ONLY.
    pub(crate) const HKB_CHARACTER: usize = 0x30;
    /// `rootMotion`, a `FloatVector4`. The engine's own per-frame displacement for this
    /// character, which is exactly the "is it actually going anywhere" question the watchdog
    /// needs and cannot get from position deltas (co-location moves the player every frame
    /// regardless). Cross-checked.
    pub(crate) const ROOT_MOTION: usize = 0x30;
}

/// `CSChrDataModule`.
pub(crate) mod chr_data_module {
    /// `hp`, `i32`. Proven by `CSChrDataModule::GetHpRate` being `[+0x138] / [+0x13c]`.
    /// Cross-checked.
    pub(crate) const HP: usize = 0x138;
}

/// `CSChrPhysicsModule`.
pub(crate) mod chr_physics_module {
    /// The live physics-space position, a `FloatVector4`. This is the field
    /// `ChrIns::GetPhysicsPosition` returns and the one `ForceSetPosition` writes -- read the
    /// creature's, write the player's, ZERO conversion, same space. Cross-checked.
    pub(crate) const POSITION: usize = 0x70;
    /// `standingOnSolidGround`, a `bool`. This is what `ChrIns::IsStandingOnSolidGround` reads,
    /// so the question "does the release point already qualify as ground" is a field read rather
    /// than a sphere cast. Cross-checked.
    pub(crate) const STANDING_ON_SOLID_GROUND: usize = 0x92;
    /// `lastGroundedPosition`, engine-maintained. By construction a point this character stood
    /// on, which is the free fallback when it dies airborne. RE-ONLY (`unk150` in the crate).
    pub(crate) const LAST_GROUNDED_POSITION: usize = 0x150;
    /// `orientationEuler`, a `FloatVector4` whose `.y` is yaw in radians -- the same convention
    /// `ChrCtrl.ragdollRotation` expects, so co-location can copy it across without touching a
    /// quaternion. Cross-checked.
    pub(crate) const ORIENTATION_EULER: usize = 0x2d0;
}

/// `ComManipulator`, and the `AiIns` reached through it.
pub(crate) mod manipulator {
    /// `operator delete(this, 0x170)` in `ComManipulator`'s scalar deleting destructor. The thunk
    /// mirrors exactly this many bytes so that a field read the engine performs on what it
    /// believes is a `ComManipulator` lands on our zeroed memory rather than past the end of it.
    pub(crate) const COM_SIZE: usize = 0x170;
    /// `ComManipulator.aiIns`.
    pub(crate) const AI_INS: usize = 0xc0;
}

/// `AiIns`, and the `AiPathData` it points at. Offsets read out of the 1.16.2 named Ghidra dump's
/// own curated structures (`getStructure AiIns` / `AiPathData`), not guessed.
/// # Three findings recorded here rather than as constants
///
/// * `turnTarget` (`+0xdab0`) is an `AiTargetPointType` -- **a 4-byte ENUM naming WHICH known
///   point to face, not a yaw and not a position.** `UpdateMovement` consumes it as
///   `FUN_1402c9410(aiIns, aiIns->turnTarget)`. With goal selection no-oped those points are not
///   refreshed, so writing it steers nothing; there is no constant for it because using it would
///   be a mistake.
/// * `motionMult` (`+0xc410`, `FloatVector4`) and `walkType` (`+0xc424`, `int`) are the gait
///   levers `[vt+0x50]` consumes after `UpdateMovement`. Untouched by this layer.
/// * Every offset in this module was read out of the 1.16.2 named dump's own curated structures.
///   **`AiIns` is `0xf0d0` bytes and the 1.17 sweep did not cover it**, so a single inserted field
///   anywhere below `+0xc3e0` would move all of them silently. `crate::possess::game` therefore
///   refuses to write any of them unless `pathData` reads back as a plausible heap pointer AND the
///   current target reads back as finite floats -- one validated field vouching for the
///   neighbourhood, which is the strongest check available without a runtime probe.
pub(crate) mod ai_ins {
    /// `wantToMoveTo`, a `FloatVector4` in physics space.
    pub(crate) const WANT_TO_MOVE_TO: usize = 0xc3e0;
    /// `pathData`, an `AiPathData*`. Doubles as the layout canary; see the module note above.
    pub(crate) const PATH_DATA: usize = 0xd9c8;
}

/// `AiPathData`.
pub(crate) mod ai_path_data {
    /// `target`, a `FloatVector4`, at the very front of the struct.
    pub(crate) const TARGET: usize = 0x0;
}

/// `WorldChrManDbg`.
pub(crate) mod world_chr_man_dbg {
    /// `camOverrideChrIns`. Cross-checked.
    ///
    /// Only five sites read it, and the one that matters is `WorldChrManImp::GetMainPlayerIns`,
    /// which prefers it over the raw `mainPlayerIns` field. That moves the ~40 `GetMainPlayerIns`
    /// consumers -- camera and lock-on -- and leaves the ~670 identity/damage/save consumers
    /// (which go through `IsMainPlayerIns`, comparing against the RAW field) pointing at the real
    /// `PlayerIns`. That split IS the safety property.
    pub(crate) const CAM_OVERRIDE_CHR_INS: usize = 0xb8;
}

/// The `ChrIns.debugFlags` offset FOR THE RUNNING BUILD, or `None` when the build is one nobody
/// has measured.
///
/// # Why this refuses rather than guessing
///
/// The two known answers are eight bytes apart, and eight bytes past 1.16.2's `debugFlags` is
/// 1.17's -- so a wrong guess in either direction lands on a real, live field
/// (`stamina_recovery` one way, `debugFlags`'s neighbour the other). There is no crash to notice
/// and no log line to read; the body simply keeps attacking, or a counter starts drifting.
/// A third build gets `None`, the neuter is skipped, and the log says so.
#[must_use]
pub(crate) fn debug_flags_offset(version: Option<FileVersion>) -> Option<usize> {
    match version? {
        v if v == SUPPORTED_FILE_VERSION => Some(chr_ins::DEBUG_FLAGS_1162),
        v if v == FILE_VERSION_1170 => Some(chr_ins::DEBUG_FLAGS_1170),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_debug_flags_offset_is_known_for_both_supported_builds_and_nothing_else() {
        assert_eq!(
            debug_flags_offset(Some(SUPPORTED_FILE_VERSION)),
            Some(0x530),
            "1.16.2"
        );
        assert_eq!(
            debug_flags_offset(Some(FILE_VERSION_1170)),
            Some(0x538),
            "1.17 -- IT MOVED"
        );
        // A build nobody measured, and the host, where there is no game image at all.
        assert_eq!(
            debug_flags_offset(Some(FileVersion {
                major: 2,
                minor: 8,
                build: 0,
                revision: 0,
            })),
            None
        );
        assert_eq!(debug_flags_offset(None), None);
    }

    /// The two answers must stay eight bytes apart and in that order; a transposition here is the
    /// shape of the bug the gate exists to prevent.
    #[test]
    fn the_two_debug_flags_offsets_differ_by_exactly_one_pointer() {
        assert_eq!(
            chr_ins::DEBUG_FLAGS_1170 - chr_ins::DEBUG_FLAGS_1162,
            core::mem::size_of::<usize>()
        );
    }

    /// The neuter must not set the no-move bit: the body is driven by the co-location write, and
    /// freezing its move vector would make it fight that write every frame.
    #[test]
    fn the_two_debug_flag_bits_are_distinct_and_only_no_attack_is_used() {
        assert_ne!(chr_ins::DEBUG_FLAG_NO_ATTACK, chr_ins::DEBUG_FLAG_NO_MOVE);
        assert_eq!(chr_ins::DEBUG_FLAG_NO_ATTACK, 0x10);
        assert_eq!(chr_ins::DEBUG_FLAG_NO_MOVE, 0x20);
    }

    /// The mute and the stop-in-flight one-shot are separate bits of one byte, and the engine's
    /// own writer sets them together as `0xC0` -- which is what possession start writes.
    #[test]
    fn the_two_sound_bits_are_distinct_and_pair_into_0xc0() {
        assert_eq!(chr_ins::MUTE_SOUND, 0x40);
        assert_eq!(chr_ins::STOP_PLAYING_SOUNDS, 0x80);
        assert_eq!(chr_ins::MUTE_SOUND & chr_ins::STOP_PLAYING_SOUNDS, 0);
        assert_eq!(chr_ins::MUTE_SOUND | chr_ins::STOP_PLAYING_SOUNDS, 0xc0);
        // Unmuting must clear the mute and leave the rest of the byte alone.
        assert_eq!(!chr_ins::MUTE_SOUND, 0xbf);
    }

    /// The proxy drain is two independent requests and co-location needs both.
    #[test]
    fn the_proxy_flags_are_one_bit_each() {
        assert_eq!(
            chr_ctrl::CHR_PROXY_FLAG_POSITION | chr_ctrl::CHR_PROXY_FLAG_ROTATION,
            3
        );
        assert_eq!(
            chr_ctrl::CHR_PROXY_FLAG_POSITION & chr_ctrl::CHR_PROXY_FLAG_ROTATION,
            0
        );
    }

    /// The override slot must sit past everything else we touch on a `ChrCtrl`, which is the
    /// cheap arithmetic proof that it is a field of its own and not an alias of one of them.
    #[test]
    fn the_manipulator_override_is_not_an_alias_of_any_field_we_write() {
        for other in [
            chr_ctrl::OWNER,
            chr_ctrl::MANIPULATOR,
            chr_ctrl::MODIFIER,
            chr_ctrl::CHR_PROXY_FLAGS,
            chr_ctrl::RAGDOLL_POSITION,
            chr_ctrl::RAGDOLL_ROTATION,
        ] {
            assert!(
                chr_ctrl::MANIPULATOR_OVERRIDE > other,
                "{other:#x} is not below the override slot"
            );
        }
        // ...and the two 16-byte vectors do not overlap each other.
        assert_eq!(
            chr_ctrl::RAGDOLL_ROTATION - chr_ctrl::RAGDOLL_POSITION,
            0x10
        );
    }
}
