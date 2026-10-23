//! The live side: read the creature's size, build this DLL's own `LockCamParam` row for it, serve
//! that copy through the getter detour, and point `ChrExFollowCam+0x468` at the id it is served
//! under. Everything reversible, everything reversed on release.
//!
//! # It writes no param row
//!
//! The resident `LockCamParam` table is only read: once for the row the camera was using (the base
//! the copy starts from) and once per candidate id, to make sure the id the copy is served under is
//! not a row. The copy lives in this DLL's memory and reaches the game through
//! [`crate::camera::getter`]. That matters beyond tidiness: Seamless Co-op's `lobby_key` is a
//! fingerprint of the param tables as loaded, and this layer used to patch a resident row for the
//! length of a possession, which put the player in a matchmaking pool of one for that long.
//!
//! The one game write left is `ChrExFollowCam+0x468`, a camera field nothing in the game writes
//! after the constructor sets it to -1.
//!
//! # Why serving a row is the mechanism, rather than writing camera state
//!
//! `CS::ChrExFollowCam::ApplyZoomLerp` (2.7.2.0 `0x1403b7570`) runs every frame from
//! `ChrExFollowCam::Update`, calls `LookupLockCamParam` afresh each time, and re-derives distance,
//! pivot height, pitch minimum, FOV and chase rate from the row it gets back. Writing the derived
//! camera state directly would be overwritten before the next frame drew; a row the lookup returns
//! is read by the same code every frame with no fight. That the lookup is per-frame is also what
//! makes the copy live: there is no load-time copy of the game's own to miss.
//!
//! # The ways this can go wrong, and what each does instead
//!
//! * **An id nothing serves freezes the camera.** `LookupLockCamParam` returns a null row for an
//!   id that is not in the table, and `ApplyZoomLerp` then skips its whole update. So `+0x468` is
//!   written only after the copy is published, and on release it goes back before the copy stops
//!   being served. A frame that loaded `+0x468` just before the release can still miss once,
//!   which keeps the previous frame's distance and pivot for one frame.
//! * **The getter could not be hooked** -- a build other than 2.7.2.0, bytes that do
//!   not match the windows `build.rs` generated, or MinHook declining. The possession keeps the
//!   vanilla framing and the derived file names the refusal; the log has the reason.
//! * **A rebuilt `ChrExFollowCam`.** The constructor writes `+0x464|+0x468 = -1`, so a camera
//!   rebuilt mid-possession (a warp, a map load) would drop the override, and one rebuilt at a new
//!   address would leave the old slot pointing at freed memory. [`Session::reassert`] re-resolves
//!   the camera every frame and puts the id back into whichever camera is live, the same shape as
//!   the camera-override re-assert the driver already does for `WorldChrManDbg+0xb8`.

use eldenring::cs::{CSPersCam, LockCamParam, SoloParam, SoloParamRepository, WorldChrMan};
use er_game_base::game_build::game_file_version;
use er_game_base::mem::{safe_read_f32, safe_read_i32, safe_read_usize};
use fromsoftware_shared::FromStatic;

use crate::camera::derived;
use crate::camera::geometry::{Refusal, Report, shape};
use crate::camera::getter;
use crate::camera::layout::{self, Offsets};
use crate::camera::served::{self, pick_served_id};
use crate::log::possess_log;
use crate::possess::layout::{chr_ins, modules};
use crate::settings::CameraSettings;

/// Compile-time cross-check, same idea as [`crate::possess::game`]'s.
///
/// `ChrCam.chrExFollowCam` is a private field in the crate, so it cannot be named by
/// `offset_of!`. It is the field immediately after `ChrCam`'s `CSPersCam` superclass, though, so
/// the superclass's size is its offset -- and that the crate and this table agree on where the
/// follow camera lives is the thing worth failing the build over.
///
/// `WorldChrMan.chr_cam` is not cross-checked: the crate still models the pre-2.7.2.0 layout, in
/// which it sits `0x10` before [`layout::world_chr_man::CHR_CAM`], so it is read at the table's
/// offset instead.
const _: () = {
    assert!(core::mem::size_of::<CSPersCam>() == layout::chr_cam::EX_FOLLOW_CAM);
};

/// `LockCamParam` row 0 -- the player's own camera row, and the fallback base when the id the
/// camera resolved last frame is unreadable. Its `camDistTarget` 3.8 and `camFovY` 48 are the
/// modal values across the whole 166-row table.
const PLAYER_LOCK_CAM_ROW: u32 = 0;

/// Everything that has to be put back, captured before anything was written.
struct Installed {
    /// The id the copy is served under, which is not a row of `LockCamParam`.
    served_id: u32,
    /// The offsets for the running build, kept so the per-frame re-assert does not look them up.
    offsets: Offsets,
    /// `ChrExFollowCam+0x468` on the camera that holds the override now. Moves when the camera is
    /// rebuilt somewhere else; see [`Session::reassert`].
    override_slot: usize,
    /// What that slot held before -- `-1` on a camera the game constructed and nothing has
    /// touched, which is every camera, because nothing in the game writes this field.
    original_param_id: i32,
}

/// One possession's worth of camera adaptation.
///
/// Constructed even when nothing is installed, because the report is the product too: a possession
/// whose camera did not change has to say why in the derived file.
pub(crate) struct Session {
    report: Report,
    installed: Option<Installed>,
    /// The subject, kept so a config reload can rebuild the copy without the driver handing it
    /// back.
    chr_ins: usize,
    /// ...and its `NpcParam` id / 10000, for the per-character `camera_distance_scale` lookup.
    chr_id: u32,
    /// The `[camera]` table this was built from.
    settings: CameraSettings,
    /// `[chr.cNNNN].camera_distance_scale` at the same moment.
    distance_scale: f32,
    /// `crate::config::generation()` when the two above were read. See [`Self::refresh`].
    generation: usize,
}

impl Session {
    /// Adapt the camera to `chr_ins`, reading the settings that decide how.
    pub(crate) fn begin(chr_ins: usize, chr_id: u32) -> Self {
        let generation = crate::config::generation();
        let (settings, distance_scale) = live_settings(chr_id);
        Self::install(
            chr_ins,
            chr_id,
            settings,
            distance_scale,
            generation,
            None,
            None,
        )
    }

    /// Re-apply after a config reload moved something, so `[camera]` is live the way
    /// `[movement].speed_scale` is: save the file, watch the framing change.
    ///
    /// Rebuilds the copy through the same code path a fresh possession takes and publishes it over
    /// the old one, without putting `+0x468` back in between -- so the camera goes straight from
    /// the old framing to the new one. When the rebuild refuses (the edit set `enabled = false`,
    /// say), the old one is undone in full. Answers whether anything was re-applied.
    pub(crate) fn refresh(&mut self) -> bool {
        let generation = crate::config::generation();
        if generation == self.generation {
            return false;
        }
        self.generation = generation;
        let (settings, distance_scale) = live_settings(self.chr_id);
        if settings == self.settings && distance_scale == self.distance_scale {
            return false;
        }
        // The base row is carried over, not re-read: `ChrExFollowCam+0x460` names the served id
        // by now, which is not a row, so re-reading it would fall back to row 0 and drop whatever
        // map-region row the possession started on.
        let base = self.report.base_row;
        let previous = self.installed.take();
        let next = Self::install(
            self.chr_ins,
            self.chr_id,
            settings,
            distance_scale,
            generation,
            base,
            // The value the camera held before the possession, not the served id it holds now:
            // restoring the served id on release would leave the camera asking for a row nothing
            // serves.
            previous.as_ref().map(|previous| previous.original_param_id),
        );
        if next.installed.is_none()
            && let Some(previous) = previous
        {
            restore_installed(&previous);
        }
        *self = next;
        true
    }

    /// Adapt the camera to `chr_ins`, or work out why not.
    ///
    /// Never panics and never leaves half an install behind: everything that can refuse is asked
    /// before the copy is published, and the one write that can still fail after it withdraws it.
    fn install(
        chr_ins: usize,
        chr_id: u32,
        settings: CameraSettings,
        distance_scale: f32,
        generation: usize,
        base_override: Option<u32>,
        original_override: Option<i32>,
    ) -> Self {
        let refuse = |reason: Refusal| Self {
            report: Report::refused(distance_scale, reason),
            installed: None,
            chr_ins,
            chr_id,
            settings,
            distance_scale,
            generation,
        };

        if !settings.enabled {
            return refuse(Refusal::Disabled);
        }
        let Some(offsets) = layout::offsets(game_file_version()) else {
            return refuse(Refusal::UnmeasuredBuild);
        };
        let Some((height, radius)) = hit_extents(chr_ins, offsets) else {
            return refuse(Refusal::NoHeight);
        };
        let Some(shape) = shape(height, radius, distance_scale, settings) else {
            return refuse(Refusal::NoHeight);
        };
        let Some(follow_cam) = follow_cam(offsets) else {
            return refuse(Refusal::NoFollowCam);
        };
        let override_slot = follow_cam + offsets.lock_cam_param_override;
        let original_param_id = match original_override {
            Some(original) => original,
            None => match unsafe { safe_read_i32(override_slot) } {
                Some(original) => served::original_override(original),
                None => return refuse(Refusal::WriteFailed),
            },
        };

        // Safety: the singleton reference is only handed out when the pointer is populated, and
        // every row read below is inside the param heap that reference owns. The holder is checked
        // first because `get` panics on a param that has not streamed in -- `get_param_file` does
        // `holder.get_res_cap(0).expect(..)`. Nothing here takes a mutable reference: the
        // resident table is read, never written.
        let Ok(repo) = (unsafe { SoloParamRepository::instance() }) else {
            return refuse(Refusal::ParamsNotReady);
        };
        if !holder_ready::<LockCamParam>(repo) {
            return refuse(Refusal::ParamsNotReady);
        }
        let Some(served_id) = pick_served_id(|id| repo.get::<LockCamParam>(id).is_some()) else {
            return refuse(Refusal::NoFreeId);
        };
        // Always taken: `pick_served_id` only answers ids that are positive as an `i32`.
        let Ok(wanted) = i32::try_from(served_id) else {
            return refuse(Refusal::NoFreeId);
        };

        // The fields the size law does not decide -- FOV, the pitch minimum, the lock vertical
        // offset, the chase rate, the lock-on radii -- come from whatever row the camera resolved
        // a frame ago, so a possession started in a map region that narrowed the camera keeps the
        // region's numbers for everything but distance and pivot.
        //
        // `rotRangeMinX` is on that list deliberately. It used to be written, lerped towards -15
        // degrees for a large subject on the theory that a tall creature needs more overhead --
        // but it is the limit on how far below the subject the camera may drop, not above (see
        // `crate::camera::geometry`), so that bought nothing and cost a shot.
        let base_row = base_override
            .filter(|base| *base != served_id)
            .unwrap_or_else(|| base_row(follow_cam, offsets, served_id));
        let Some(mut copy) = repo.get::<LockCamParam>(base_row).cloned() else {
            return refuse(Refusal::BaseRowMissing);
        };
        copy.set_cam_dist_target(shape.distance);
        copy.set_chr_org_offset_y(shape.pivot_height);

        // Last of the refusals, because it is the first thing with a side effect: the first time
        // through, it patches the getter's entry.
        if !getter::ensure_installed() {
            return refuse(Refusal::HookUnavailable);
        }
        // Published before `+0x468` names the id, so no frame asks for an id nothing serves.
        getter::publish(served_id, copy);
        if !write_i32(override_slot, wanted) {
            // Nothing names the id, so nothing would ask for the copy; withdrawn anyway, so
            // "nothing we started outlives the attempt" holds without reasoning about it.
            getter::withdraw();
            return refuse(Refusal::WriteFailed);
        }

        Self {
            report: Report {
                hit_height: Some(height),
                hit_radius: Some(radius),
                served_id: Some(served_id),
                base_row: Some(base_row),
                distance_scale,
                applied: Some(shape),
                refusal: None,
            },
            installed: Some(Installed {
                served_id,
                offsets,
                override_slot,
                original_param_id,
            }),
            chr_ins,
            chr_id,
            settings,
            distance_scale,
            generation,
        }
    }

    /// Put the override back if something reset it, into whichever camera is live now.
    ///
    /// Nothing in the game writes `+0x468`, but the `ChrExFollowCam` constructor sets it to -1 as
    /// half of one qword store, so a camera rebuilt mid-possession loses the override -- and one
    /// rebuilt at a different address would leave a remembered slot pointing into freed memory.
    /// So the camera is resolved again every frame: three fault-tolerant reads, a compare, and a
    /// write only when the id is missing.
    pub(crate) fn reassert(&mut self) {
        let Some(installed) = self.installed.as_mut() else {
            return;
        };
        let Ok(wanted) = i32::try_from(installed.served_id) else {
            return;
        };
        let Some(follow_cam) = follow_cam(installed.offsets) else {
            return;
        };
        installed.override_slot = follow_cam + installed.offsets.lock_cam_param_override;
        if unsafe { safe_read_i32(installed.override_slot) } != Some(wanted) {
            write_i32(installed.override_slot, wanted);
        }
    }

    /// Undo everything, in the reverse order it was done. Answers whether it all took.
    ///
    /// Safe to call when nothing was installed, which is what makes it a teardown step the state
    /// machine can run unconditionally.
    pub(crate) fn restore(&mut self) -> bool {
        let Some(installed) = self.installed.take() else {
            return true;
        };
        let restored = restore_installed(&installed);
        if !restored {
            possess_log(format_args!(
                "camera: restore incomplete -- ChrExFollowCam+0x468 could not be put back to {}, \
                 so a camera still naming id {} misses on that id until the next map load \
                 rebuilds it. This mod's LockCamParam copy is no longer served either way.",
                installed.original_param_id, installed.served_id,
            ));
        }
        restored
    }

    /// What to print in `er-npc-possess.derived.toml`.
    pub(crate) fn derived_block(&self, chr_id: u32) -> String {
        derived::render(chr_id, &self.report)
    }

    /// One line for `er-npc-possess.log`, said once at possession start.
    pub(crate) fn log_line(&self) -> String {
        match (self.report.applied, self.report.refusal) {
            (Some(shape), _) => {
                let height = self.report.hit_height.unwrap_or_default();
                let framing = crate::camera::geometry::framing(shape, height, 0.0);
                format!(
                    "camera: hitHeight {height:.2} m -> this mod's LockCamParam copy (dist {:.2} \
                     m, pivot {:.2} m, the rest from row {}) served under id {}, which the table \
                     does not contain, and ChrExFollowCam+0x468 pointed at it; no param row was \
                     written. That frames the top of the body at {:+.4} half-screen-heights with \
                     {:.2} body-heights above it; the player's own is +0.0296 / 1.09.",
                    shape.distance,
                    shape.pivot_height,
                    self.report.base_row.unwrap_or(PLAYER_LOCK_CAM_ROW),
                    self.report.served_id.unwrap_or_default(),
                    framing.head_screen_y,
                    framing.headroom_heights,
                )
            }
            (None, Some(reason)) => format!(
                "camera: not adapted -- {}. The creature is framed with the parameters your own \
                 body would have used, which on anything large puts the camera inside the model. \
                 See {}.",
                reason.describe(),
                crate::config::DERIVED_CONFIG_FILE_NAME,
            ),
            (None, None) => "camera: not adapted, and for no recorded reason".to_owned(),
        }
    }
}

/// Put `+0x468` back and stop serving the copy, in that order. Answers whether the slot took.
///
/// Slot first: from the next frame on, `ApplyZoomLerp` asks for the camera's own id again, and a
/// frame that already loaded the served id still gets the copy. The other order would make that
/// frame miss. The slot is resolved afresh rather than remembered, for the reason
/// [`Session::reassert`] gives. The copy is withdrawn whatever the slot did, and never freed.
fn restore_installed(installed: &Installed) -> bool {
    let slot = follow_cam(installed.offsets)
        .map(|follow_cam| follow_cam + installed.offsets.lock_cam_param_override);
    let restored = slot.is_some_and(|slot| write_i32(slot, installed.original_param_id));
    getter::withdraw();
    restored
}

/// The two settings the size law needs, read together so they describe one instant of the file.
fn live_settings(chr_id: u32) -> (CameraSettings, f32) {
    let scale = crate::config::chr_override(chr_id)
        .and_then(|over| over.camera_distance_scale)
        .unwrap_or(1.0);
    (crate::config::camera(), scale)
}

/// Is `P`'s res cap streamed in? `rows` and `get` panic when it is not.
fn holder_ready<P: SoloParam>(repo: &SoloParamRepository) -> bool {
    repo.solo_param_holders
        .get(P::INDEX as usize)
        .and_then(|holder| holder.get_res_cap(0))
        .is_some()
}

/// The row id whose fields the copy is built on: whatever the camera resolved last frame, read
/// out of the `ChrExFollowCam+0x460` mirror `ApplyZoomLerp` writes for free.
///
/// Falls back to the player row when the mirror is unreadable, negative, or the served id -- which
/// is not a row, and is what the mirror holds during a possession.
fn base_row(follow_cam: usize, offsets: Offsets, served_id: u32) -> u32 {
    let mirrored = unsafe { safe_read_i32(follow_cam + offsets.resolved_lock_cam_param) };
    match mirrored.and_then(|id| u32::try_from(id).ok()) {
        Some(id) if id != served_id => id,
        _ => PLAYER_LOCK_CAM_ROW,
    }
}

/// `WorldChrMan.chrCam -> +0x60 chrExFollowCam`, or `None` when either link has not come up.
fn follow_cam(offsets: Offsets) -> Option<usize> {
    // Safety: `instance()` yields a reference only when the singleton is populated, and both raw
    // reads after it are fault-tolerant. `chrCam` is read at the table's offset rather than
    // through the crate's typed `chr_cam` field, because 2.7.2.0 moved it `+0x10` and the crate
    // still models the earlier layout (see `layout::world_chr_man::CHR_CAM`).
    let world_chr_man = unsafe { WorldChrMan::instance() }.ok()?;
    let base = core::ptr::from_ref(world_chr_man) as usize;
    let chr_cam = unsafe { safe_read_usize(base + offsets.chr_cam) }.filter(|&p| p != 0)?;
    let follow = unsafe { safe_read_usize(chr_cam + offsets.ex_follow_cam) }?;
    // A follow camera that is not a plausible heap pointer means the ChrCam is half-constructed;
    // `+0x468` on a bad base is a write into whatever happens to be there.
    unsafe { er_game_base::mem::is_heap_aligned_ptr(follow) }.then_some(follow)
}

/// Where the player is looking: the camera's own yaw, or `None` when the camera is not up.
///
/// One `f32` at `ChrExFollowCam+0x154 anglesEuler.y`, which the engine derives as
/// `atan2(look.x, look.z)` -- see [`layout::chr_ex_follow_cam::ANGLES_EULER_YAW`] for the four
/// lines of `Update` that compute it and the byte window that pins it.
///
/// **The sign is not a character heading.** A body facing this direction has yaw
/// `anglesEuler.y + PI`; [`crate::possess::intent::aim`] is the only place that conversion is
/// made, so there is one copy of it.
///
/// # Why this answers the lock-on case too
///
/// Lock-on drives this camera -- `ChrExFollowCam::Update` derives these angles from the point the
/// camera is aimed at, and a held lock is what moves that point. So "where the camera looks"
/// already points at the locked target, which is why [`crate::possess::intent::aim`] treats the
/// camera as the always-available answer and the subject's own `ChrIns+0xd0 lockOnTargetPos` as a
/// refinement it accepts only when the two agree.
pub(crate) fn look_yaw() -> Option<f32> {
    let offsets = layout::offsets(game_file_version())?;
    let follow_cam = follow_cam(offsets)?;
    // Safety: the read goes through `ReadProcessMemory`, so an unmapped address answers `None`
    // instead of faulting.
    let yaw = unsafe { safe_read_f32(follow_cam + offsets.angles_euler_yaw) }?;
    yaw.is_finite().then_some(yaw)
}

/// `[[ChrIns+0x190] + 0x68] + 0x340/0x344` -- the physics capsule's height and radius, in metres.
///
/// This is what `CS::ChrIns::GetPhysicsHitHeight` returns, reached by the two loads the function
/// itself performs rather than by calling it.
fn hit_extents(chr_ins: usize, offsets: Offsets) -> Option<(f32, f32)> {
    let modules = unsafe { safe_read_usize(chr_ins + chr_ins::MODULES) }?;
    let physics = unsafe { safe_read_usize(modules + modules::PHYSICS) }?;
    let height = unsafe { safe_read_f32(physics + offsets.hit_height) }?;
    let radius = unsafe { safe_read_f32(physics + offsets.hit_radius) }?;
    Some((height, radius))
}

/// Write an `i32`, but only after proving the address reads. Same contract as the writers in
/// [`crate::possess::game`]: a read faults harmlessly, a write does not get the chance.
fn write_i32(at: usize, value: i32) -> bool {
    if unsafe { safe_read_i32(at) }.is_none() {
        return false;
    }
    unsafe { (at as *mut i32).write(value) };
    true
}
