//! Everything read out of the live game: who else is in the session, where they are, whether you
//! can see them, and where the camera is looking.
//!
//! Read-only throughout. This DLL never writes a byte of game memory, never patches a param
//! (which is what breaks Seamless invasions -- bd `param-patching-dlls-break-seamless-invasions`)
//! and installs no detour. The only game FUNCTIONS it calls are the navmesh query in
//! [`crate::navpath`] and the physics raycast below, both of which the engine treats as queries.

#![cfg(windows)]

use eldenring::cs::{CSCamera, CSHavokMan, ChrIns, PlayerIns, WorldChrMan};
use eldenring::position::{HavokPosition, PositionDelta};
use fromsoftware_shared::FromStatic;

use crate::geometry::{self, Camera};

/// Height above a character's physics origin that the sight ray leaves from and arrives at.
///
/// The physics position is at the character's FEET. A ray between two pairs of feet clips every
/// pebble and low step between them and reports "blocked" across an open field, which would keep
/// the near-suppression rule from ever firing. Chest height is what a player means by "I can see
/// them".
const SIGHT_HEIGHT_METERS: f32 = 1.2;

/// Height above the physics origin that the "no route" arrow leaves the body at, so it emerges
/// from the character rather than from the dirt they are standing on.
const ARROW_ORIGIN_HEIGHT_METERS: f32 = 1.1;

/// How far above a waypoint the drawn line floats.
///
/// The navmesh sits ON the collision surface, and a line drawn exactly on it z-fights with the
/// ground it is describing. A few centimetres reads as painted on the ground; more reads as
/// hovering above it.
const PATH_LIFT_METERS: f32 = 0.06;

/// Ray filter the game itself passes when it casts against world geometry for a player --
/// `1403fba04: MOV EDX, 0x2000058`, the call site immediately above
/// `CS::CSPhysWorld::CastRay`. Reusing the engine's own mask is what makes "blocked" mean the
/// same thing here as it does to the game.
const SIGHT_RAY_FILTER: u32 = 0x0200_0058;

/// One other player in the session, reduced to what the overlay needs.
///
/// There is deliberately no `ChrIns` pointer here. The route is planned for a body the size of
/// YOURS -- you are the one who has to walk it -- so the navmesh agent is always the local
/// player, and keeping a remote pointer around would only invite planning the route for the wrong
/// body.
pub(crate) struct RemotePlayer {
    /// The engine's own name for this character, used to keep a path's colour stable across
    /// frames even as players enter and leave.
    pub(crate) field_ins_handle: u64,
    /// Physics-space position, at the feet.
    pub(crate) position: [f32; 3],
    /// Straight-line distance from the local player, in metres.
    pub(crate) distance_meters: f32,
    /// True when a ray from your chest to theirs reaches them unobstructed.
    pub(crate) in_sight: bool,
}

/// The local player, and everyone else worth drawing a path to.
pub(crate) struct Roster {
    pub(crate) local_chr_ins: usize,
    pub(crate) local_field_ins_handle: u64,
    pub(crate) local_position: [f32; 3],
    pub(crate) remotes: Vec<RemotePlayer>,
}

fn to_array(position: HavokPosition) -> [f32; 3] {
    [position.0, position.1, position.2]
}

/// The `FieldInsHandle`'s eight raw bytes, which is the form the navmesh agent descriptor wants.
///
/// `FieldInsHandle` is `repr(C, align(8))` over a `u32` selector and an `i32` block id, so the
/// little-endian qword the engine copies is `selector | (block_id << 32)`. The block id is cast
/// through `u32` rather than sign-extended: `-1` is a legitimate "no block" value, and
/// sign-extending it would set the whole upper half and make two different handles compare equal.
fn handle_bits(chr_ins: &ChrIns) -> u64 {
    let handle = chr_ins.field_ins_handle;
    u64::from(handle.selector.0) | (u64::from(handle.block_id.0 as u32) << 32)
}

/// A character's physics position, or `None` when its physics module is not built yet.
///
/// A character mid-spawn has a module whose position is still the origin, and drawing a path to
/// `(0, 0, 0)` is a line across the entire map -- far more misleading than drawing nothing.
fn physics_position(chr_ins: &ChrIns) -> Option<[f32; 3]> {
    let position = to_array(chr_ins.modules.physics.position);
    position
        .iter()
        .all(|axis| axis.is_finite())
        .then_some(position)
        .filter(|position| geometry::length(*position) > f32::EPSILON)
}

/// Is this character loaded far enough to have a real position and a real body?
fn is_live(chr_ins: &ChrIns) -> bool {
    // Dead players are still in the ChrSet with a valid position; a path to a corpse is noise.
    chr_ins.modules.data.hp > 0
}

/// Read the session roster.
///
/// Returns `None` before the world exists -- at the title screen, during a load, and in any menu
/// state where there is no local player to draw from.
///
/// # Safety
///
/// Must be called on the game thread.
pub(crate) unsafe fn roster(max_targets: usize) -> Option<Roster> {
    // SAFETY: singleton access on the game thread; `Err` before the world is up.
    let world_chr_man = unsafe { WorldChrMan::instance() }.ok()?;
    let main_player = world_chr_man.main_player.as_ref()?;
    let local_position = physics_position(&main_player.chr_ins)?;
    let local_chr_ins = std::ptr::from_ref(&main_player.chr_ins) as usize;

    let mut remotes = Vec::new();
    for player in world_chr_man.player_chr_set.characters() {
        let player: &PlayerIns = player;
        let chr_ins = &player.chr_ins;
        // The local player is in this ChrSet too, and a path from yourself to yourself is a dot.
        if std::ptr::from_ref(chr_ins) as usize == local_chr_ins {
            continue;
        }
        if !is_live(chr_ins) {
            continue;
        }
        let Some(position) = physics_position(chr_ins) else {
            continue;
        };
        let distance_meters = geometry::length(geometry::sub(position, local_position));
        remotes.push(RemotePlayer {
            field_ins_handle: handle_bits(chr_ins),
            position,
            distance_meters,
            // Filled below: the raycast needs the main player as its owner, and doing it here
            // would hold a borrow of the ChrSet across a game call.
            in_sight: false,
        });
    }

    // Nearest first, so the cap keeps the players who matter rather than whichever slot the
    // ChrSet happened to hand back first.
    remotes.sort_by(|a, b| {
        a.distance_meters
            .partial_cmp(&b.distance_meters)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    remotes.truncate(max_targets);

    for remote in &mut remotes {
        // SAFETY: game thread; both positions came from live physics modules.
        remote.in_sight =
            unsafe { has_line_of_sight(main_player, local_position, remote.position) };
    }

    Some(Roster {
        local_chr_ins,
        local_field_ins_handle: handle_bits(&main_player.chr_ins),
        local_position,
        remotes,
    })
}

/// Can a ray from `from` reach `to` without hitting world geometry?
///
/// Both ends are lifted to [`SIGHT_HEIGHT_METERS`]. A cast that reports no hit is clear sight; a
/// cast that hits something *past* the target is also clear sight, because the engine's raycast
/// reports the first hit along the whole extent rather than stopping at a distance.
///
/// # Safety
///
/// Must be called on the game thread with a live `PlayerIns`.
unsafe fn has_line_of_sight(owner: &PlayerIns, from: [f32; 3], to: [f32; 3]) -> bool {
    // SAFETY: singleton access on the game thread.
    let Ok(havok_man) = (unsafe { CSHavokMan::instance() }) else {
        // With no physics world there is no evidence of a wall, and claiming sight would suppress
        // the path. Claiming NO sight draws it, which is the harmless direction to be wrong in.
        return false;
    };
    let eye = [from[0], from[1] + SIGHT_HEIGHT_METERS, from[2]];
    let target = [to[0], to[1] + SIGHT_HEIGHT_METERS, to[2]];
    let delta = geometry::sub(target, eye);
    let span = geometry::length(delta);
    if span <= f32::EPSILON {
        return true;
    }
    let origin = HavokPosition(eye[0], eye[1], eye[2], 0.0);
    // SAFETY: a query into the physics world; it writes only the caller's out-parameter.
    let hit = havok_man.phys_world.cast_ray(
        SIGHT_RAY_FILTER,
        &origin,
        PositionDelta(delta[0], delta[1], delta[2]),
        owner,
    );
    match hit {
        None => true,
        Some(point) => {
            // Anything struck within the span blocks; the target's own body sits at the far end,
            // so a small tolerance keeps a hit ON the target from reading as a wall.
            const TARGET_TOLERANCE_METERS: f32 = 0.75;
            let struck = geometry::length(geometry::sub([point.0, point.1, point.2], eye));
            struck >= span - TARGET_TOLERANCE_METERS
        }
    }
}

/// The live camera, in the form [`crate::geometry`] projects with.
///
/// Read from `CSCamera::pers_cam_1`, whose `matrix` is the camera-to-world transform: rows 0..2
/// are the right/up/forward basis and row 3 is the eye. Returns `None` whenever the camera is not
/// up or its numbers are not finite, which is every frame before the world exists.
///
/// # Safety
///
/// Reads live game memory. Called from the render callback, where a torn read costs one frame's
/// worth of wrong geometry and nothing worse.
pub(crate) unsafe fn camera() -> Option<Camera> {
    // SAFETY: singleton access; `Err` before the camera exists.
    let cameras = unsafe { CSCamera::instance() }.ok()?;
    let cam = &cameras.pers_cam_1;
    let matrix = cam.matrix;
    let camera = Camera {
        right: [matrix.0.0, matrix.0.1, matrix.0.2],
        up: [matrix.1.0, matrix.1.1, matrix.1.2],
        forward: [matrix.2.0, matrix.2.1, matrix.2.2],
        eye: [matrix.3.0, matrix.3.1, matrix.3.2],
        fov_y: cam.fov,
        aspect: cam.aspect_ratio,
    };
    let finite = camera
        .right
        .iter()
        .chain(camera.up.iter())
        .chain(camera.forward.iter())
        .chain(camera.eye.iter())
        .all(|axis| axis.is_finite());
    if !finite || !camera.fov_y.is_finite() || !camera.aspect.is_finite() {
        return None;
    }
    // An identity-ish or zeroed basis is what the struct holds before the first real frame;
    // projecting through it puts every path at the screen's centre.
    if geometry::length(camera.forward) < 0.5 {
        return None;
    }
    Some(camera)
}

/// Where the "no route" arrow leaves the player's body.
pub(crate) fn arrow_origin(local_position: [f32; 3]) -> [f32; 3] {
    [
        local_position[0],
        local_position[1] + ARROW_ORIGIN_HEIGHT_METERS,
        local_position[2],
    ]
}

/// Lift a navmesh waypoint clear of the surface it describes.
pub(crate) fn lift_waypoint(point: [f32; 3]) -> [f32; 3] {
    [point[0], point[1] + PATH_LIFT_METERS, point[2]]
}
