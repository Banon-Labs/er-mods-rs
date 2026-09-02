//! EVERY TOUCH OF LIVE GAME MEMORY THE ENGINE MAKES, and nothing that decides anything.
//!
//! # What is deliberately NOT here
//!
//! **Almost no native calls: ONE game function address in the whole module, and no detour.** Every
//! lever the possession itself needs turned out to be a field: `EnableInvincible`'s whole body is
//! `chrFlags1c5 = (chrFlags1c5 & 0xef) | (state << 4)`, `SetDisableFallDamage`'s is
//! `MOV [RCX+0x1B],DL; RET`, the move is the engine's own per-frame proxy drain rather than a
//! teleport call, and firing an attack is a write to `CSChrEventModule+0x18`.
//!
//! The single exception is [`PLAY_ANIMATION_BY_BEHAVIOR_NAME_RVA`], and it was not a free choice.
//! The field write formats `W_Event%04d` and nothing else, and `W_Event` is a broad alias layer
//! rather than a total one -- so every dodge in the game (2,252 of them, spelled `W_Step`) is
//! unreachable without it. Keeping the property would have meant shipping a possession that cannot
//! roll. That address is registered as a verified 1.16.2 -> 1.17 pair and resolved through
//! `game_rva_named`, so on an unrecognised build the fallback goes quiet rather than jumping into
//! whatever now occupies those bytes.
//!
//! Everything else here is still translation-proof; what it CAN be broken by is a struct offset,
//! which is why [`crate::possess::layout`] is one module and why the assertions below exist.
//!
//! # The two singletons, and why neither is an address either
//!
//! `WorldChrMan` and `WorldChrManDbg` come from `fromsoftware-rs`'s `FromStatic`, which resolves
//! them through the game's own DLRF reflection data BY NAME. That is build-independent by
//! construction: there is no RVA to go stale.
//!
//! # Reads fault, writes do not get the chance
//!
//! Every read of game memory goes through `er_game_base::mem::safe_read_*`, which is
//! `ReadProcessMemory` against the current-process pseudo-handle -- a despawned character or a
//! half-constructed pointer chain answers `None` instead of raising an access violation. A WRITE
//! cannot be made fault-tolerant that way, so every write here reads the same address first and
//! skips when the read fails. That turns a stale pointer into a missed frame rather than a crash.

use eldenring::cs::{
    CSChrBehaviorModule, CSChrDataModule, CSChrEventModule, CSChrPhysicsModule, CSChrTimeActModule,
    CSChrTimeActModuleAnim, ChrCtrl as CsChrCtrl, ChrIns as CsChrIns, ChrInsModuleContainer,
    ChrSet, ChrType, WorldChrMan, WorldChrManDbg,
};
use er_game_base::mem::{
    game_rva_named, is_heap_aligned_ptr, safe_read_f32, safe_read_i32, safe_read_u8,
    safe_read_usize,
};
use fromsoftware_shared::FromStatic;

use crate::possess::intent::{IntentWrite, Stick};
use crate::possess::layout::{
    ai_ins, ai_path_data, chr_behavior_module, chr_ctrl, chr_ctrl_modifier, chr_data_module,
    chr_event_module, chr_ins, chr_physics_module, chr_time_act_module, manipulator, modules,
    world_chr_man_dbg,
};
use crate::settings::{TargetMode, TargetSettings};

/// COMPILE-TIME CROSS-CHECK of two independently derived layouts.
///
/// The left side is this crate's reverse engineering; the right is `fromsoftware-rs`'s model of
/// the same struct. They were derived separately, so an assertion failing here means one of them
/// moved and somebody has to go and find out which -- which is a build error, and exactly the
/// noise that is wanted. Only the crate's `pub` fields can be checked; the constants it spells
/// `unkNNN` (`ChrCtrl+0x100/+0x110/+0x3b0`, `CSChrPhysicsModule+0x150`) carry no assertion and are
/// marked RE-ONLY in `layout`.
const _: () = {
    assert!(core::mem::offset_of!(CsChrIns, chr_ctrl) == chr_ins::CHR_CTRL);
    assert!(core::mem::offset_of!(CsChrIns, modules) == chr_ins::MODULES);
    assert!(core::mem::offset_of!(CsChrIns, chr_flags1c5) == chr_ins::CHR_FLAGS_1C5);
    assert!(core::mem::offset_of!(CsChrIns, chr_flags1c8) == chr_ins::CHR_FLAGS_1C8);
    assert!(
        core::mem::offset_of!(CsChrIns, tint_alpha_multiplier) == chr_ins::TINT_ALPHA_MULTIPLIER
    );
    assert!(
        core::mem::offset_of!(CsChrIns, tint_alpha_multiplier_modifier)
            == chr_ins::TINT_ALPHA_MULTIPLIER_MODIFIER
    );
    // The crate models the 1.16.2 position of `debugFlags`; the engine picks 1.16.2 or 1.17 at
    // runtime. Asserting the crate against the 1.16.2 constant is what pins the pair.
    assert!(core::mem::offset_of!(CsChrIns, debug_flags) == chr_ins::DEBUG_FLAGS_1162);
    assert!(core::mem::offset_of!(CsChrCtrl, owner) == chr_ctrl::OWNER);
    assert!(core::mem::offset_of!(CsChrCtrl, manipulator) == chr_ctrl::MANIPULATOR);
    assert!(core::mem::offset_of!(CsChrCtrl, modifier) == chr_ctrl::MODIFIER);
    assert!(core::mem::offset_of!(CsChrCtrl, chr_proxy_flags) == chr_ctrl::CHR_PROXY_FLAGS);
    assert!(core::mem::offset_of!(ChrInsModuleContainer, data) == modules::DATA);
    assert!(core::mem::offset_of!(ChrInsModuleContainer, physics) == modules::PHYSICS);
    assert!(core::mem::offset_of!(ChrInsModuleContainer, time_act) == modules::TIME_ACT);
    assert!(core::mem::offset_of!(ChrInsModuleContainer, behavior) == modules::BEHAVIOR);
    assert!(core::mem::offset_of!(ChrInsModuleContainer, event) == modules::EVENT);
    assert!(
        core::mem::offset_of!(CSChrEventModule, request_animation_id)
            == chr_event_module::REQUEST_ANIMATION_ID
    );
    assert!(
        core::mem::offset_of!(CSChrTimeActModule, anim_queue) == chr_time_act_module::ANIM_QUEUE
    );
    assert!(core::mem::offset_of!(CSChrTimeActModule, read_idx) == chr_time_act_module::READ_IDX);
    assert!(core::mem::size_of::<CSChrTimeActModuleAnim>() == chr_time_act_module::ANIM_STRIDE);
    assert!(
        core::mem::offset_of!(CSChrBehaviorModule, root_motion) == chr_behavior_module::ROOT_MOTION
    );
    assert!(core::mem::offset_of!(CSChrDataModule, hp) == chr_data_module::HP);
    assert!(core::mem::offset_of!(CSChrPhysicsModule, position) == chr_physics_module::POSITION);
    assert!(
        core::mem::offset_of!(CSChrPhysicsModule, standing_on_solid_ground)
            == chr_physics_module::STANDING_ON_SOLID_GROUND
    );
    assert!(
        core::mem::offset_of!(CSChrPhysicsModule, orientation_euler)
            == chr_physics_module::ORIENTATION_EULER
    );
    assert!(
        core::mem::offset_of!(WorldChrManDbg, cam_override_chr_ins)
            == world_chr_man_dbg::CAM_OVERRIDE_CHR_INS
    );
};

/// `CS::PlayAnimationByBehaviorName`, 1.16.2 RVA `0xc14370`.
///
/// The ONLY game function address this crate resolves. Layers 1 and 2 resolve none at all -- every
/// lever they need turned out to be a field -- and layer 3 keeps that for everything the
/// `W_Event%04d` field write can spell -- 4,667 of the 6,921 shipped moves. This exists for the
/// remainder, which is almost entirely the 2,252 dodges: they are spelled `W_Step` and have no
/// `W_Event` name at all, so without this they are simply not in the game.
///
/// Verified for 1.17 rather than assumed. The function is 80 bytes and BYTE-IDENTICAL between
/// 1.16.2 `0x140c14370` and 1.17 `0x140c15a40`, matching uniquely in both images with no rel32
/// wildcarding needed -- both of its callees moved by the same `+0x16d0` as the function itself.
/// The pair is registered in `docs/recon/rva-map-1162-to-1170.verified.tsv`, which is what makes
/// [`game_rva_named`] able to translate it; without that row it refuses and the fallback goes
/// quiet rather than jumping into whatever now occupies those bytes.
const PLAY_ANIMATION_BY_BEHAVIOR_NAME_RVA: u32 = 0x00c1_4370;

/// `void PlayAnimationByBehaviorName(hkbCharacter** slot, const wchar_t* name)`.
///
/// The first argument is a POINTER TO the slot, not the character -- the function opens with
/// `CMP qword ptr [RCX],0x0` and later `MOV RCX,[RBX]`. Ghidra types it `hkbCharacter*`, which is
/// wrong and would have been a null-deref on the first call.
type PlayAnimationByBehaviorNameFn = unsafe extern "system" fn(*mut usize, *const u16);

/// A `FloatVector4` in the engine's layout, `align(16)` because `ForceSetPosition` loads its
/// argument with `MOVAPS`, which `#GP`s on an unaligned address.
///
/// Shared with `er-invasion-warp-core`, which carries the alignment test and discovered the
/// requirement; the dependency exists for this one type rather than duplicating a `repr` whose
/// whole point is a property nobody would notice was missing until the game died.
pub(crate) use er_invasion_warp_core::warp::FloatVector4;

/// Read three floats as a position. `None` when the address will not read.
fn read_vec3(at: usize) -> Option<[f32; 3]> {
    let x = unsafe { safe_read_f32(at) }?;
    let y = unsafe { safe_read_f32(at + 4) }?;
    let z = unsafe { safe_read_f32(at + 8) }?;
    (x.is_finite() && y.is_finite() && z.is_finite()).then_some([x, y, z])
}

/// Write a `u32`, but only after proving the address reads.
fn write_u32(at: usize, value: u32) -> bool {
    if unsafe { safe_read_i32(at) }.is_none() {
        return false;
    }
    unsafe { (at as *mut u32).write(value) };
    true
}

/// Write a byte, but only after proving the address reads.
fn write_u8(at: usize, value: u8) -> bool {
    if unsafe { safe_read_u8(at) }.is_none() {
        return false;
    }
    unsafe { (at as *mut u8).write(value) };
    true
}

/// Read-modify-write one flag byte.
fn set_bits_u8(at: usize, set: u8, clear: u8) -> bool {
    let Some(current) = (unsafe { safe_read_u8(at) }) else {
        return false;
    };
    write_u8(at, (current | set) & !clear)
}

/// Write an `f32`, but only after proving the address reads.
fn write_f32(at: usize, value: f32) -> bool {
    if unsafe { safe_read_f32(at) }.is_none() {
        return false;
    }
    unsafe { (at as *mut f32).write(value) };
    true
}

/// Write a 16-byte vector, after proving both halves read.
fn write_vec4(at: usize, value: FloatVector4) -> bool {
    if unsafe { safe_read_usize(at) }.is_none() || unsafe { safe_read_usize(at + 8) }.is_none() {
        return false;
    }
    // The address is a field of a 16-aligned game struct at a 16-aligned offset, so this is a
    // sound aligned store; `FloatVector4` is `repr(C, align(16))` for the same reason.
    unsafe { (at as *mut FloatVector4).write(value) };
    true
}

/// One live `ChrIns`, addressed by number rather than by reference.
///
/// A `usize` and not a `&ChrIns`: the possessed creature can despawn between frames, and a Rust
/// reference to it would be a claim of validity this code cannot make. Every accessor re-walks the
/// pointer chain through `safe_read_*` and answers `None` when a link has gone.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Chr(usize);

impl Chr {
    pub(crate) const fn new(address: usize) -> Self {
        Self(address)
    }

    pub(crate) const fn address(self) -> usize {
        self.0
    }

    /// The `ChrCtrl`, verified to point back at this `ChrIns`.
    ///
    /// The back-check is not ceremony: `ChrCtrl+0x3b0` is a write into a structure this code found
    /// by following two pointers, and `owner` is a free proof that the second one landed where it
    /// was supposed to.
    pub(crate) fn chr_ctrl(self) -> Option<usize> {
        let ctrl = unsafe { safe_read_usize(self.0 + chr_ins::CHR_CTRL) }?;
        if !unsafe { is_heap_aligned_ptr(ctrl) } {
            return None;
        }
        let owner = unsafe { safe_read_usize(ctrl + chr_ctrl::OWNER) }?;
        (owner == self.0).then_some(ctrl)
    }

    fn module(self, slot: usize) -> Option<usize> {
        let container = unsafe { safe_read_usize(self.0 + chr_ins::MODULES) }?;
        if !unsafe { is_heap_aligned_ptr(container) } {
            return None;
        }
        let module = unsafe { safe_read_usize(container + slot) }?;
        unsafe { is_heap_aligned_ptr(module) }.then_some(module)
    }

    fn physics(self) -> Option<usize> {
        self.module(modules::PHYSICS)
    }

    /// Physics-space position. The same field `ForceSetPosition` writes, so reading one
    /// character's and writing another's needs no conversion at all.
    pub(crate) fn position(self) -> Option<[f32; 3]> {
        read_vec3(self.physics()? + chr_physics_module::POSITION)
    }

    /// Heading in radians, from `orientationEuler.y`.
    pub(crate) fn yaw(self) -> Option<f32> {
        let at = self.physics()? + chr_physics_module::ORIENTATION_EULER;
        unsafe { safe_read_f32(at + 4) }.filter(|v| v.is_finite())
    }

    /// The engine-maintained last position this character was standing on.
    pub(crate) fn last_grounded_position(self) -> Option<[f32; 3]> {
        read_vec3(self.physics()? + chr_physics_module::LAST_GROUNDED_POSITION)
    }

    /// Is it on solid ground right now? The field `ChrIns::IsStandingOnSolidGround` reads.
    pub(crate) fn standing_on_solid_ground(self) -> Option<bool> {
        let at = self.physics()? + chr_physics_module::STANDING_ON_SOLID_GROUND;
        unsafe { safe_read_u8(at) }.map(|byte| byte != 0)
    }

    /// Current HP, from `CSChrDataModule+0x138`.
    pub(crate) fn hp(self) -> Option<i32> {
        let data = self.module(modules::DATA)?;
        unsafe { safe_read_i32(data + chr_data_module::HP) }
    }

    /// FIRE AN ANIMATION, which for this crate means writing one `int`.
    ///
    /// `CSChrEventModule::RequestAnimation`'s entire lasting effect is this store; see
    /// [`chr_event_module`] for why the other half of it does not need doing. The engine picks the
    /// request up in its next `CSChrEventModule::Update`, turns the id into the behaviour-graph
    /// event `W_Event%04d`, and resets the field to -1.
    ///
    /// Returns whether the store landed -- NOT whether the animation played. Nothing in the
    /// process can answer the second question: an id the graph does not consume resolves cleanly
    /// and does nothing. That is what the offline fireability gate and the watchdog are between
    /// them for.
    pub(crate) fn request_animation(self, animation: i32) -> bool {
        let Some(event) = self.module(modules::EVENT) else {
            return false;
        };
        write_u32(
            event + chr_event_module::REQUEST_ANIMATION_ID,
            animation as u32,
        )
    }

    /// Is a request still waiting to be consumed?
    ///
    /// Used to avoid stacking two requests in one frame: the field holds ONE id, so a second
    /// write before `Update` runs silently discards the first.
    pub(crate) fn animation_request_pending(self) -> bool {
        let Some(event) = self.module(modules::EVENT) else {
            return false;
        };
        unsafe { safe_read_i32(event + chr_event_module::REQUEST_ANIMATION_ID) }
            .is_some_and(|pending| pending != -1)
    }

    /// The animation playing right now, from the TimeAct ring buffer's read cursor.
    pub(crate) fn current_animation(self) -> Option<i32> {
        let time_act = self.module(modules::TIME_ACT)?;
        let read = unsafe { safe_read_i32(time_act + chr_time_act_module::READ_IDX) }?;
        let index = u32::try_from(read).ok()?;
        if index >= chr_time_act_module::ANIM_QUEUE_LEN {
            // The cursor is a `u32` the engine wraps itself, so an out-of-range value means the
            // pointer chain landed somewhere wrong. Reading past the ring would be reading
            // whatever follows it.
            return None;
        }
        let entry = time_act
            + chr_time_act_module::ANIM_QUEUE
            + (index as usize) * chr_time_act_module::ANIM_STRIDE;
        unsafe { safe_read_i32(entry) }
    }

    /// The creature's `hkbCharacter`, by two loads.
    ///
    /// `CSChrEventModule::Update` gets this by calling `FUN_14041aef0(behaviorModule, &slot)`,
    /// which calls `FUN_140c07a40(behaviorModule->field_0x10, &slot)`, whose whole body is
    /// `slot = *(field_0x10 + 0x30)` behind a null check. Both calls are pure loads, so this does
    /// the loads and resolves neither address -- the moveset layer ends up needing exactly one
    /// game function address, and this is not it.
    fn hkb_character(self) -> Option<usize> {
        let behavior = self.module(modules::BEHAVIOR)?;
        let owner = unsafe { safe_read_usize(behavior + chr_behavior_module::HKB_OWNER) }?;
        if !unsafe { is_heap_aligned_ptr(owner) } {
            return None;
        }
        let character = unsafe { safe_read_usize(owner + chr_behavior_module::HKB_CHARACTER) }?;
        unsafe { is_heap_aligned_ptr(character) }.then_some(character)
    }

    /// FIRE AN ANIMATION BY NAME -- the fallback for ids the field write cannot spell.
    ///
    /// `requestAnimationId` formats `W_Event%04d` and nothing else, and `W_Event` is a broad alias
    /// layer rather than a total one: c2120's dodges exist only as `W_Step6000`..`W_Step6011` and
    /// have no `W_Event` spelling at all. This builds the name the graph actually declares --
    /// `<prefix><id>` at `%04d` -- and hands it to `PlayAnimationByBehaviorName`, which resolves it
    /// through the same behaviour world and reaches the same `fireHkbEvent_C`.
    ///
    /// **THIS IS THE ONE GAME FUNCTION ADDRESS THE WHOLE CRATE RESOLVES**, and it is on this path
    /// only. Everything the `W_Event` spelling can reach goes through
    /// [`Self::request_animation`], which is a field write. Refusing costs the dodges; calling an
    /// unresolved address costs the session, so a refusal returns `false` and the caller logs it.
    pub(crate) fn play_animation_by_name(self, name: &[u16]) -> bool {
        debug_assert_eq!(
            name.last(),
            Some(&0),
            "the game expects a NUL-terminated wide string"
        );
        let Some(mut character) = self.hkb_character() else {
            return false;
        };
        let Ok(address) = game_rva_named(
            PLAY_ANIMATION_BY_BEHAVIOR_NAME_RVA,
            "PLAY_ANIMATION_BY_BEHAVIOR_NAME_RVA",
        ) else {
            return false;
        };
        // Safety: the address was resolved for the running build immediately above, and the
        // signature is read out of the function's own disassembly rather than inferred --
        // `CMP qword ptr [RCX],0x0` proves the first argument is a POINTER TO the slot holding the
        // `hkbCharacter`, not the character itself, which is why `character` is passed by address.
        let play: PlayAnimationByBehaviorNameFn = unsafe { core::mem::transmute(address) };
        unsafe { play(&raw mut character, name.as_ptr()) };
        true
    }

    /// The creature's chr id -- `4500` for a Flying Dragon -- from its `NpcParam` row.
    ///
    /// `NpcParam` rows are `<chr><4 digits>`, so the id is the row divided by ten thousand. That
    /// is the same arithmetic `scripts/er-moveset-table-gen.py` uses to key the shipped table, and
    /// it is the reason the table can be keyed by an integer at all.
    ///
    /// The offset comes from `offset_of!` rather than from [`crate::possess::layout`], unlike
    /// every other field here. The layout module exists for offsets the `eldenring` crate does
    /// NOT model, where a hand-derived constant is the only option and a cross-check is the best
    /// available guard. This field IS modelled, so taking the offset from the type is strictly
    /// better than writing the number down twice and asserting they match.
    pub(crate) fn npc_param_id(self) -> Option<u32> {
        let at = self.0 + core::mem::offset_of!(CsChrIns, npc_param_id);
        let row = unsafe { safe_read_i32(at) }?;
        u32::try_from(row).ok().map(|row| row / 10_000)
    }

    /// Squared magnitude of the engine's own per-frame root motion for this character.
    ///
    /// Squared because the watchdog only compares it against a threshold, and because the
    /// alternative -- differencing positions between frames -- is useless here: co-location moves
    /// the player every frame whether or not the creature is going anywhere.
    pub(crate) fn root_motion_squared(self) -> Option<f32> {
        let behavior = self.module(modules::BEHAVIOR)?;
        let [x, y, z] = read_vec3(behavior + chr_behavior_module::ROOT_MOTION)?;
        Some(z.mul_add(z, x.mul_add(x, y * y)))
    }

    /// `CS::ChrIns::IsDead` -- `chrCtrl->ctrlModifier->ChrCtrlModifierData._1cFlags & 1`.
    pub(crate) fn is_dead_flag(self) -> Option<bool> {
        let ctrl = self.chr_ctrl()?;
        let modifier = unsafe { safe_read_usize(ctrl + chr_ctrl::MODIFIER) }?;
        if !unsafe { is_heap_aligned_ptr(modifier) } {
            return None;
        }
        let at = modifier + chr_ctrl_modifier::DATA + chr_ctrl_modifier::FLAGS_1C;
        unsafe { safe_read_i32(at) }
            .map(|flags| (flags as u32) & chr_ctrl_modifier::FLAG_IS_DEAD != 0)
    }

    /// Dead by either signal. HP is the direct one and is checked first; the flag is the engine's
    /// own predicate and catches a death that has not yet zeroed the counter.
    pub(crate) fn is_dead(self) -> bool {
        if self.hp().is_some_and(|hp| hp <= 0) {
            return true;
        }
        self.is_dead_flag().unwrap_or(false)
    }

    /// Does the whole pointer chain the engine needs still resolve?
    pub(crate) fn is_live(self) -> bool {
        self.chr_ctrl().is_some() && self.position().is_some()
    }

    /// The creature's REAL `ComManipulator`, read from `ChrCtrl+0x18` -- never from `+0x3b0`,
    /// which is where OUR thunk goes.
    pub(crate) fn real_manipulator(self) -> Option<usize> {
        let ctrl = self.chr_ctrl()?;
        let manip = unsafe { safe_read_usize(ctrl + chr_ctrl::MANIPULATOR) }?;
        unsafe { is_heap_aligned_ptr(manip) }.then_some(manip)
    }

    /// The `AiIns`, reached through the REAL manipulator.
    ///
    /// Through `ChrCtrl+0x18` on purpose: `ChrIns::GetAiInsFromManipulator` and
    /// `EnemyIns::GetChrManipulator` do not consult the `+0x3b0` override, so the AI side of the
    /// engine still sees the creature's own `ComManipulator` -- which is exactly what lets us
    /// write intent into fields the forwarded `[vt+0x50]` will then consume.
    pub(crate) fn ai_ins(self) -> Option<usize> {
        let ai = unsafe { safe_read_usize(self.real_manipulator()? + manipulator::AI_INS) }?;
        unsafe { is_heap_aligned_ptr(ai) }.then_some(ai)
    }

    /// Install the thunk on `ChrCtrl+0x3b0`.
    pub(crate) fn install_manipulator_override(self, thunk: usize) -> bool {
        let Some(ctrl) = self.chr_ctrl() else {
            return false;
        };
        let at = ctrl + chr_ctrl::MANIPULATOR_OVERRIDE;
        // Refuse to install over anything: a non-null slot means either a second copy of this mod
        // or an assumption that has stopped holding, and both are worse to overwrite than to
        // decline.
        if unsafe { safe_read_usize(at) } != Some(0) {
            return false;
        }
        unsafe { (at as *mut usize).write(thunk) };
        true
    }

    /// Clear `ChrCtrl+0x3b0`. THE STEP THAT MUST HAPPEN: `ChrCtrl::Unref` DLPanics on a non-null
    /// slot, so leaving it armed is a crash whenever the character is next unloaded.
    ///
    /// Returns `true` when the slot is null afterwards -- including when the whole chain has gone,
    /// because a `ChrCtrl` that no longer resolves cannot be holding our pointer either.
    pub(crate) fn clear_manipulator_override(self, expected: usize) -> bool {
        let Some(ctrl) = self.chr_ctrl() else {
            return true;
        };
        let at = ctrl + chr_ctrl::MANIPULATOR_OVERRIDE;
        match unsafe { safe_read_usize(at) } {
            None => true,
            Some(0) => true,
            // Only OUR pointer is ours to remove.
            Some(current) if current == expected => {
                unsafe { (at as *mut usize).write(0) };
                true
            }
            Some(_) => false,
        }
    }

    /// Co-locate: ask the engine to move this character to `position` facing `yaw`, on its own
    /// next `updatePos`.
    ///
    /// **Nothing is called.** This fills the same request buffer the engine already drains every
    /// frame for network ghosts -- `ChrCtrl::UpdatePositions` reads `chrProxyFlags`, calls
    /// `ForceSetPosition` and `SetOrientation`, syncs the havok proxies, zeroes the accumulated
    /// delta matrix and clears the flags. Two consequences worth naming:
    ///
    /// * **No fall damage.** `ForceSetPosition` snaps `prevUpdatePosition` as well as `position`,
    ///   so the per-frame fall delta is zero rather than the distance we just moved.
    /// * **Other players see it.** The same drain pushes the result into
    ///   `WorldChrManImp::GetNetChrSyncPositionUpdateBuffer`, so an UNMODDED client sees the
    ///   possessing player genuinely standing at the creature.
    ///
    /// `yaw` is EULER RADIANS in `.y`, not a quaternion: the drain feeds `SetOrientation`, which
    /// feeds `EulerToQuat`.
    pub(crate) fn request_move(self, position: [f32; 3], yaw: Option<f32>) -> bool {
        let Some(ctrl) = self.chr_ctrl() else {
            return false;
        };
        if !position.iter().all(|v| v.is_finite()) {
            return false;
        }
        let mut flags = chr_ctrl::CHR_PROXY_FLAG_POSITION;
        if !write_vec4(
            ctrl + chr_ctrl::RAGDOLL_POSITION,
            FloatVector4::new(position[0], position[1], position[2], 1.0),
        ) {
            return false;
        }
        if let Some(yaw) = yaw.filter(|v| v.is_finite())
            && write_vec4(
                ctrl + chr_ctrl::RAGDOLL_ROTATION,
                FloatVector4::new(0.0, yaw, 0.0, 0.0),
            )
        {
            flags |= chr_ctrl::CHR_PROXY_FLAG_ROTATION;
        }
        let at = ctrl + chr_ctrl::CHR_PROXY_FLAGS;
        let existing = unsafe { safe_read_i32(at) }.unwrap_or(0) as u32;
        write_u32(at, existing | flags)
    }

    /// Invincibility, exactly as `CS::ChrIns::EnableInvincible` writes it.
    pub(crate) fn set_invincible(self, on: bool) -> bool {
        let at = self.0 + chr_ins::CHR_FLAGS_1C5;
        if on {
            set_bits_u8(at, chr_ins::INVINCIBLE, 0)
        } else {
            set_bits_u8(at, 0, chr_ins::INVINCIBLE)
        }
    }

    /// Set the tint alpha AND zero its modifier.
    ///
    /// Both, because the modifier is a per-frame DECAY: `SetFadeInOut` writes `-alpha/duration`
    /// into it, which is why calling that function fades rather than holds. Writing the alpha
    /// alone would work for one frame and then drift.
    pub(crate) fn set_alpha(self, alpha: f32) -> bool {
        let held = write_f32(self.0 + chr_ins::TINT_ALPHA_MULTIPLIER_MODIFIER, 0.0);
        write_f32(self.0 + chr_ins::TINT_ALPHA_MULTIPLIER, alpha) && held
    }

    /// Silence this character, and optionally stop what it is already playing.
    ///
    /// `stop_playing` is a one-shot the engine consumes in `PostPhysicsSafe`, so it is passed
    /// `true` once at possession start and `false` on every per-frame re-assert.
    pub(crate) fn set_muted(self, muted: bool, stop_playing: bool) -> bool {
        let at = self.0 + chr_ins::CHR_FLAGS_1C8;
        if muted {
            let set = if stop_playing {
                chr_ins::MUTE_SOUND | chr_ins::STOP_PLAYING_SOUNDS
            } else {
                chr_ins::MUTE_SOUND
            };
            set_bits_u8(at, set, 0)
        } else {
            set_bits_u8(at, 0, chr_ins::MUTE_SOUND)
        }
    }

    /// Set or clear the no-attack debug flag, and always clear no-move.
    ///
    /// `offset` comes from `layout::debug_flags_offset`, which answers `None` on a build nobody
    /// measured -- so an unrecognised build never reaches this function and never writes eight
    /// bytes off target into a live field.
    ///
    /// No-move is cleared unconditionally rather than left alone: the body is driven by
    /// [`Self::request_move`], and a zeroed move vector would fight that write every frame.
    pub(crate) fn set_no_attack(self, offset: usize, on: bool) -> bool {
        let at = self.0 + offset;
        let Some(current) = (unsafe { safe_read_i32(at) }) else {
            return false;
        };
        let mut flags = current as u32 & !chr_ins::DEBUG_FLAG_NO_MOVE;
        if on {
            flags |= chr_ins::DEBUG_FLAG_NO_ATTACK;
        } else {
            flags &= !chr_ins::DEBUG_FLAG_NO_ATTACK;
        }
        write_u32(at, flags)
    }

    /// Does this creature's `AiIns` still look like the structure the offsets describe?
    ///
    /// **THE LAYOUT CANARY.** `AiIns` is `0xf0d0` bytes, its field offsets come from the 1.16.2
    /// named dump, and the 1.17 verification sweep did not cover it -- so a single inserted field
    /// would move `wantToMoveTo` and `pathData` silently, and the struct is far too big for a
    /// stray write to fault. `pathData` is a pointer, which is the one field in the neighbourhood
    /// whose value can be checked for plausibility, so it vouches for its neighbours: if the
    /// offsets are wrong, `+0xd9c8` is overwhelmingly likely to hold something that is not a
    /// heap-aligned pointer to memory containing three finite floats.
    ///
    /// This is not proof. It is the strongest check available without a runtime probe, and it
    /// fails CLOSED -- movement intent is simply not written.
    pub(crate) fn ai_path_target(self) -> Option<usize> {
        let path_data = unsafe { safe_read_usize(self.ai_ins()? + ai_ins::PATH_DATA) }?;
        if !unsafe { is_heap_aligned_ptr(path_data) } {
            return None;
        }
        let target = path_data + ai_path_data::TARGET;
        read_vec3(target).map(|_| target)
    }

    /// Write this frame's movement intent into BOTH `wantToMoveTo` and `pathData->target`.
    ///
    /// Both, with the same value, because neither wins in every branch of
    /// `AiIns::UpdateMovement`; see [`crate::possess::intent`] for the three-way branch this is
    /// answering. Gated on [`Self::ai_path_target`], so a build whose `AiIns` layout has moved
    /// gets no writes at all rather than two wrong ones.
    pub(crate) fn write_move_intent(self, write: IntentWrite) -> bool {
        let Some(target_at) = self.ai_path_target() else {
            return false;
        };
        let Some(ai) = self.ai_ins() else {
            return false;
        };
        let value = FloatVector4::new(write.target[0], write.target[1], write.target[2], 1.0);
        let path = write_vec4(target_at, value);
        let want = write_vec4(ai + ai_ins::WANT_TO_MOVE_TO, value);
        path && want
    }

    /// The physics capsule's horizontal half-extent, in metres -- `CSChrPhysicsModule+0x344`.
    ///
    /// THE FIELD THAT DECIDES OVERLAP, which is why the spawn layer reads it and why the camera
    /// layer (which cares how TALL the subject is) reads its neighbour instead. Both offsets are
    /// the camera layer's, deliberately: they are one pair of numbers with one proof, and a second
    /// copy here would be a second thing to be wrong about.
    ///
    /// Populated by `CSChrPhysicsModule::InitForEnemy` from `NpcParam.hitRadius`, so it does not
    /// read until the character is built -- which is exactly why the spawn layer places the
    /// creature after readiness rather than at creation.
    pub(crate) fn hit_radius(self) -> Option<f32> {
        let at = self.physics()? + crate::camera::layout::chr_physics_module::HIT_RADIUS;
        unsafe { safe_read_f32(at) }.filter(|v| v.is_finite())
    }

    /// Tell the engine this character was last standing HERE.
    ///
    /// **THIS IS A FALL-DEATH SAFETY WRITE AND NOT A COSMETIC ONE.** `CSChrFallModule`'s landing
    /// handler computes the fall it is about to charge for as
    /// `lastGroundedPosition.y - GetPosition().y` -- byte-proven in both images, uniquely, at
    /// 1.16.2 `0x14044dd1f` and 1.17 `0x14044e27f`:
    ///
    /// ```text
    /// 48 8b 88 90 01 00 00   MOV RCX,[RAX+0x190]     ; ChrIns->modules
    /// 48 8b 59 68            MOV RBX,[RCX+0x68]      ; ->physics
    /// e8 ?? ?? ?? ??         CALL GetPosition
    /// 0f 10 b3 50 01 00 00   MOVUPS XMM6,[RBX+0x150] ; lastGroundedPosition
    /// 0f c6 f6 55            SHUFPS XMM6,XMM6,0x55   ; .y
    /// f3 0f 5c 70 04         SUBSS  XMM6,[RAX+0x4]   ; - position.y
    /// ```
    ///
    /// -- and when that exceeds a global threshold the handler dispatches the fall-death call
    /// through the character's manipulator vtable. **None of it consults `IsImmuneToAttack`**, so
    /// `chrFlags1c5 & 0x10` -- the whole of the possession's body neuter as far as damage goes --
    /// does not cover it.
    ///
    /// [`Self::request_move`] snaps `position` and `prevUpdatePosition` and leaves this field
    /// alone, so a body carried through a leaping creature's arc lands reading a fall it never
    /// took. Writing it alongside the move is not a lie to the engine: the body IS being put here,
    /// so here is where it last stood.
    pub(crate) fn pin_last_grounded(self, position: [f32; 3]) -> bool {
        let Some(physics) = self.physics() else {
            return false;
        };
        if !position.iter().all(|v| v.is_finite()) {
            return false;
        }
        write_vec4(
            physics + chr_physics_module::LAST_GROUNDED_POSITION,
            FloatVector4::new(position[0], position[1], position[2], 1.0),
        )
    }
}

/// The real local player, as a `ChrIns` address.
///
/// `PlayerIns.chr_ins` is the struct's first field, so the `PlayerIns` pointer IS the `ChrIns`
/// pointer -- the same identity `RespawnPlayer` relies on.
pub(crate) fn main_player() -> Option<Chr> {
    let world_chr_man = unsafe { WorldChrMan::instance() }.ok()?;
    let player = world_chr_man.main_player.as_ref()?;
    Some(Chr::new(core::ptr::from_ref(&player.chr_ins) as usize))
}

/// Point the camera and lock-on at `chr`, or hand them back with `None`.
///
/// This writes `WorldChrManDbg+0xb8 camOverrideChrIns`, which only
/// `WorldChrManImp::GetMainPlayerIns` and four siblings read. `PlayerIns::IsMainPlayerIns`
/// compares against the RAW `WorldChrManImp+0x1e508` and ignores the override, so the ~670
/// identity, damage and save consumers keep pointing at the real player while the ~40 camera and
/// lock-on consumers follow the creature. **That split is the safety property**; repointing
/// `mainPlayerIns` instead is a proven hard blocker (`PlayerIns` is `0x740` and `EnemyIns` is
/// `0x5e0`, and `PlayerIns.playerGameData@+0x580` aliases `EnemyIns.manipulator@+0x580`, so null
/// guards never fire).
///
/// Nothing has to be cleaned up on despawn either: `WorldChrManImp::RemoveChrIns` nulls this field
/// when the character being removed is the one it names.
pub(crate) fn set_camera_override(chr: Option<Chr>) -> bool {
    let Ok(dbg) = (unsafe { WorldChrManDbg::instance() }) else {
        return false;
    };
    let at = core::ptr::from_ref(dbg) as usize + world_chr_man_dbg::CAM_OVERRIDE_CHR_INS;
    if unsafe { safe_read_usize(at) }.is_none() {
        return false;
    }
    unsafe { (at as *mut usize).write(chr.map_or(0, Chr::address)) };
    true
}

/// Is the camera currently pointed at `chr`?
pub(crate) fn camera_override_is(chr: Chr) -> bool {
    let Ok(dbg) = (unsafe { WorldChrManDbg::instance() }) else {
        return false;
    };
    let at = core::ptr::from_ref(dbg) as usize + world_chr_man_dbg::CAM_OVERRIDE_CHR_INS;
    let current = unsafe { safe_read_usize(at) };
    current == Some(chr.address())
}

/// Metres from the possessed creature to the nearest OTHER live enemy.
///
/// What the moveset dispatcher bands on. Not a lock-on distance: `CSLockTgtMan` is not modelled by
/// this crate and reaching it would need an offset nobody here has verified on 1.17, so this is
/// the nearest hostile instead and the module docs say so rather than implying otherwise.
///
/// The player's own body is excluded for free -- it is `ChrType::Player`, and co-located with the
/// creature anyway, so including it would report every attack as point-blank.
///
/// `None` means nothing is within the fifty-unit search radius, which the dispatcher reads as
/// [`crate::moveset::dispatch::Band::Far`]. Called only on a frame with a press, because it walks
/// every loaded `ChrSet`.
pub(crate) fn nearest_hostile_distance(creature: Chr) -> Option<f32> {
    let settings = TargetSettings {
        mode: TargetMode::Nearest,
        ..TargetSettings::default()
    };
    // `pick_target` excludes the character it is given by address, so passing the creature makes
    // it "nearest enemy to the creature, that is not the creature".
    let (target, _) = pick_target(settings, creature);
    let (from, to) = (creature.position()?, target?.position()?);
    let squared = (to[0] - from[0]).powi(2) + (to[1] - from[1]).powi(2) + (to[2] - from[2]).powi(2);
    squared.is_finite().then(|| squared.sqrt())
}

/// Why a target could not be found. Carried into the refusal so the player is told which of the
/// four modes looked and what it saw, rather than "no target".
pub(crate) fn describe_no_target(mode: TargetMode, candidates: usize) -> String {
    match mode {
        TargetMode::LockOn => {
            format!("nothing locked on ({candidates} enemies loaded) -- lock on and press again")
        }
        TargetMode::Nearest | TargetMode::Crosshair => {
            format!("no enemy within reach ({candidates} loaded)")
        }
        TargetMode::ChrId => format!("no loaded enemy matches chr_id ({candidates} loaded)"),
        // UNREACHABLE IN PRACTICE, and spelled out rather than swept under a `_` arm. The engine
        // returns into `begin_spawn` before any search happens, so `spawn` never reaches a
        // target-not-found path -- but a wildcard here would also silently swallow the NEXT mode
        // somebody adds, and this is the message that would be printed if the interception ever
        // stopped working.
        TargetMode::Spawn => {
            "spawn mode creates a character rather than searching for one, so this refusal means \
             the spawn path was not taken"
                .to_owned()
        }
    }
}

/// How far a `nearest`/`crosshair` search will reach, in physics-space units squared.
///
/// Bounded rather than global: possessing something on the other side of the map because it
/// happened to be the closest thing loaded is not a feature, it is a bug the player cannot see the
/// cause of. Fifty units is comfortably past melee range and short of the next encounter.
const MAX_TARGET_DISTANCE_SQUARED: f32 = 50.0 * 50.0;

/// Find the character `settings` selects, and count what was considered.
///
/// Returns `(target, candidates_seen)`.
pub(crate) fn pick_target(settings: TargetSettings, player: Chr) -> (Option<Chr>, usize) {
    let Ok(world_chr_man) = (unsafe { WorldChrMan::instance() }) else {
        return (None, 0);
    };
    // The lock-on handle lives on the `PlayerIns`, and `main_player` is an `OwnedPtr`, so the
    // dereference is explicit rather than inferred -- a `&OwnedPtr<PlayerIns>` and a `&PlayerIns`
    // are different things and the compiler is right to say so.
    let locked_on = world_chr_man
        .main_player
        .as_ref()
        .map(|player| player.locked_on_enemy);

    // The four ChrSets that hold no enemies, excluded by ADDRESS rather than by index -- an index
    // would be one more reverse-engineered constant to be wrong about. This is the same walk
    // `er-enemynpc-effects` uses, which is the only enemy enumeration in this repo with runtime
    // evidence behind it.
    let excluded = [
        core::ptr::from_ref(&world_chr_man.player_chr_set) as usize,
        core::ptr::from_ref(&world_chr_man.ghost_chr_set) as usize,
        core::ptr::from_ref(&world_chr_man.summon_buddy_chr_set) as usize,
        core::ptr::from_ref(&world_chr_man.debug_chr_set) as usize,
    ];
    let mut chr_sets: Vec<usize> = Vec::with_capacity(16);
    chr_sets.push(core::ptr::from_ref(&world_chr_man.open_field_chr_set.base) as usize);
    for chr_set in world_chr_man.chr_sets.iter().flatten() {
        let address = chr_set.as_ptr() as usize;
        if excluded.contains(&address) || chr_sets.contains(&address) {
            continue;
        }
        chr_sets.push(address);
    }

    let player_position = player.position();
    let mut candidates = 0usize;
    let mut best: Option<(f32, Chr)> = None;
    for address in chr_sets {
        let chr_set = unsafe { &*(address as *const ChrSet<CsChrIns>) };
        for chr_ins in chr_set.characters() {
            let at = core::ptr::from_ref(chr_ins) as usize;
            if at == player.address() || chr_ins.chr_type != ChrType::Npc {
                continue;
            }
            // The game leaves `special_effect` null on a character that is still being
            // constructed, and reading through it is a live crash this repo has already captured.
            // Read it as a raw pointer so a half-built character is a skip.
            let special_effect =
                unsafe { *(core::ptr::from_ref(&chr_ins.special_effect).cast::<usize>()) };
            if special_effect == 0 {
                continue;
            }
            let chr = Chr::new(at);
            if chr.is_dead() || !chr.is_live() {
                continue;
            }
            candidates += 1;

            match settings.mode {
                TargetMode::ChrId => {
                    if chr_ins.npc_param_id == settings.chr_id || chr_ins.npc_id == settings.chr_id
                    {
                        return (Some(chr), candidates);
                    }
                }
                TargetMode::LockOn => {
                    if locked_on.is_some_and(|handle| {
                        !handle.is_empty() && handle == chr_ins.field_ins_handle
                    }) {
                        return (Some(chr), candidates);
                    }
                }
                // CROSSHAIR IS NEAREST TODAY, and says so rather than pretending. Picking what the
                // camera centre is pointed at needs the camera's own forward vector, which is a
                // piece of reverse engineering this layer does not have; `nearest` is the honest
                // fallback and the mode is kept so the config does not have to change when it
                // lands.
                TargetMode::Nearest | TargetMode::Crosshair => {
                    let (Some(from), Some(to)) = (player_position, chr.position()) else {
                        continue;
                    };
                    let distance = (to[0] - from[0]).powi(2)
                        + (to[1] - from[1]).powi(2)
                        + (to[2] - from[2]).powi(2);
                    if distance <= MAX_TARGET_DISTANCE_SQUARED
                        && best.is_none_or(|(best_distance, _)| distance < best_distance)
                    {
                        best = Some((distance, chr));
                    }
                }
                // Spawn mode has no search to do: the engine intercepts it before this walk. The
                // arm is explicit rather than a wildcard so that adding a mode that DOES search is
                // a compile error here instead of a mode that silently matches nothing.
                TargetMode::Spawn => {}
            }
        }
    }
    (best.map(|(_, chr)| chr), candidates)
}

/// Read the left thumbstick as a movement request. `None` when no pad is connected, or when the
/// stick is inside the deadzone.
pub(crate) fn read_move_stick() -> Option<Stick> {
    let (x, y) = crate::input::read_left_stick()?;
    Stick::from_xinput(x, y)
}
