//! Spawning the game's own visual effects at world positions.
//!
//! Everything else in this DLL draws with imgui, in screen space, over the finished frame. That
//! is why the line looks painted on the glass: it does not occlude behind a hill, it takes no
//! light, and it is unmistakably not part of the game. An FXR spawned through the engine's own
//! SFX manager is a real effect in the world -- correct depth, correct lighting, the look the
//! game already has.
//!
//! # This is the one thing here that CHANGES the game
//!
//! The rest of this crate reads. This spawns. It is a real engine object with a real lifetime,
//! and the module-level claim that this DLL does nothing to the game stops being true the moment
//! `marker_fxr_id` is set to something other than zero. That is why it is off by default.
//!
//! Whether a Seamless Co-op session replicates one of these to other players is **not known**. If
//! it does, a trail of markers pointing at an invader is a trail pointing back at you. Until
//! somebody watches a second client while these spawn, treat that as an open question rather than
//! a solved one.

#![cfg(windows)]

use crate::log::path_log;

/// `FUN_140d929f0(uint *fxrId, FloatMatrix4x4 *worldTransform)`.
///
/// The engine's own "spawn this effect there" primitive: it resolves the SFX singleton, calls
/// `CS::CSSfxImp::SpawnFfxInstance` with the argument set the game uses for a one-shot
/// (`..., 0, 8, -1, -1, -1`), and performs the `FX4HG::FXHGSfxCtrl` construct/release bookkeeping
/// around it. Fire and forget: nothing is returned to keep or free.
///
/// This is precisely what `CS::ChrIns::SpawnOneShotSfx` calls once per spawn transform after it
/// has resolved a dummypoly into a list of them. Calling it directly is what removes the need for
/// a character or an asset to hang the effect off -- the three named `SpawnOneShotSfx` overloads
/// are all attachment-based and none of them takes a bare position.
///
/// Byte-verified against `eldenring-deobf.bin` (shift 0): `48 8b c4 55 48 8d a8 48 f8 ff ff`.
const SPAWN_FXR_AT_TRANSFORM_RVA: u32 = 0xd9_29f0;

/// `GLOBAL_CSSfx`, the `CSSfxImp` singleton.
///
/// Read and checked HERE, before the call, because the callee's own null check is a `DLPanic` --
/// `"未初期化のシングルトンにアクセスしました。"`, a subroutine that does not return. Calling the
/// spawn before the SFX manager exists would not fail, it would take the game down. The singleton
/// is absent for the whole of boot, which is exactly when a task like ours starts ticking.
///
/// From `140d92a37: mov 0x2ff0f7a(%rip),%rcx  # 0x143d839b8`, immediately followed by the
/// `test %rcx,%rcx` that guards that panic. NOT `0x143c5adb0` -- that is `__security_cookie`,
/// loaded first and immediately xor'd with `rsp`, and mistaking it for the singleton would hand
/// the engine a stack-derived value as a `this` pointer.
const GLOBAL_CSSFX_RVA: u32 = 0x3d8_39b8;

type SpawnFxrFn = unsafe extern "C" fn(*const u32, *const [f32; 16]);

/// A world transform with no rotation and no scale, positioned at `position`.
///
/// Row-major with the translation in row 3, which is the layout the engine reads everywhere else
/// in this crate -- `CSCam`'s camera-to-world matrix puts the eye in row 3 and its basis in rows
/// 0..2, and the spawn-location list `CS::ChrIns::SpawnOneShotSfx` walks is the same 0x40-byte
/// shape.
///
/// Rotation is left as identity deliberately. An upright marker reads the same from every angle,
/// and deriving a rotation from the path direction would make each marker's orientation depend on
/// a navmesh answer that changes between refreshes -- markers that visibly twitch as the route is
/// recomputed.
fn transform_at(position: [f32; 3]) -> [f32; 16] {
    [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        position[0],
        position[1],
        position[2],
        1.0,
    ]
}

/// Is the SFX manager up?
///
/// # Safety
///
/// Must be called on the game thread.
unsafe fn sfx_manager_ready() -> bool {
    let Ok(module_base) = er_game_base::mem::game_module_base() else {
        return false;
    };
    let address = module_base + GLOBAL_CSSFX_RVA as usize;
    // SAFETY: fault-tolerant read; returns None rather than faulting if the page is not mapped.
    let singleton = unsafe { er_game_base::mem::safe_read_usize(address) };
    singleton.is_some_and(|singleton| {
        // SAFETY: a plausibility screen on the raw value; it reads nothing through the pointer.
        singleton != 0 && unsafe { er_game_base::mem::is_heap_aligned_ptr(singleton) }
    })
}

/// Resolve a game function by RVA, refusing anything that is not inside the game image.
fn function(rva: u32) -> Option<usize> {
    let module_base = er_game_base::mem::game_module_base().ok()?;
    Some(module_base + rva as usize)
}

/// Spawn one effect at a world position. Returns whether the call was made.
///
/// Refuses rather than risks: a zero id, an SFX manager that is not up yet, or a coordinate that
/// is not finite all return `false` without calling the engine.
///
/// # Safety
///
/// Must be called on the game thread. The engine spawns on the calling thread and touches its own
/// allocators, so calling this from the render thread would race the game's own SFX work.
pub(crate) unsafe fn spawn_at(fxr_id: u32, position: [f32; 3]) -> bool {
    if fxr_id == 0 || !position.iter().all(|axis| axis.is_finite()) {
        return false;
    }
    // SAFETY: game thread; a fault-tolerant read of one global.
    if !unsafe { sfx_manager_ready() } {
        return false;
    }
    let Some(spawn) = function(SPAWN_FXR_AT_TRANSFORM_RVA) else {
        return false;
    };
    // SAFETY: a validated address inside the game image, called with the two arguments the
    // engine's own call site passes: a pointer to the id and a pointer to a 4x4 transform.
    let spawn: SpawnFxrFn = unsafe { std::mem::transmute::<usize, SpawnFxrFn>(spawn) };
    let transform = transform_at(position);
    let id = fxr_id;
    // SAFETY: both pointers are to live locals that outlive the call, which copies from them.
    unsafe { spawn(&raw const id, &raw const transform) };
    true
}

/// Spawn a run of markers, reporting how many were placed.
///
/// # Safety
///
/// Must be called on the game thread.
pub(crate) unsafe fn spawn_markers(fxr_id: u32, positions: &[[f32; 3]]) -> usize {
    let mut placed = 0;
    for position in positions {
        // SAFETY: game thread, as this function's own contract requires.
        if unsafe { spawn_at(fxr_id, *position) } {
            placed += 1;
        }
    }
    if placed == 0 && !positions.is_empty() {
        path_log(format_args!(
            "markers: {} position(s) and none spawned -- fxr id {fxr_id}, SFX manager up: {}",
            positions.len(),
            // SAFETY: game thread.
            unsafe { sfx_manager_ready() }
        ));
    }
    placed
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_transform_carries_the_position_in_row_three_and_is_otherwise_identity() {
        let transform = transform_at([10.0, -2.5, 7.0]);
        assert_eq!(&transform[12..15], &[10.0, -2.5, 7.0]);
        assert_eq!(transform[15], 1.0);
        // Rows 0..2 are the identity basis.
        assert_eq!(&transform[0..4], &[1.0, 0.0, 0.0, 0.0]);
        assert_eq!(&transform[4..8], &[0.0, 1.0, 0.0, 0.0]);
        assert_eq!(&transform[8..12], &[0.0, 0.0, 1.0, 0.0]);
    }

    #[test]
    fn a_transform_is_the_sixteen_floats_the_engine_reads() {
        assert_eq!(size_of::<[f32; 16]>(), 0x40);
    }
}
