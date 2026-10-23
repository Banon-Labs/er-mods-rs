//! The detour on `LookupLockCamParam(out, id)` that hands the follow camera this DLL's row copy.
//!
//! See [`crate::camera::served`] for why the row is served instead of written and why it is
//! served under an id the table lacks. This module is the part that touches the game: it finds the
//! getter on the running build, checks it is the function the windows were taken from, hooks it,
//! and answers one id.
//!
//! # The getter, as read on 2.7.2.0
//!
//! `void LookupLockCamParam(LockCamParamLookupResult* out, uint id)` (the 1.16.2 dump's name and
//! signature; 2.7.2.0 `0x140d40ba0`, `.pdata` length `0x168`). It clears `rbx`, answers a negative
//! id with `{null, id}` at once, asks
//! `GetParamResCap` for table `0x1f`, binary-searches the row index, and on every non-negative path
//! ends in `mov [rsi+8],edi; mov [rsi],rbx`: `out = {row* @+0, id @+8}`, with a null row on a
//! miss. `ApplyZoomLerp` (`0x1403b7570`) zeroes `out+0`, stores the id it resolved at
//! `out+8`, calls the getter at `0x1403b76ed`, skips its whole update when `out+0` is null, and
//! otherwise reads every field through `out+0` alone (`0x140d409a0` and its siblings are each
//! `mov rax,[rcx]; movss xmm0,[rax+N]`). Nothing turns the row pointer back into an index.
//!
//! The detour calls the getter first and then replaces `out+0` when the id is the served one, so
//! every other lookup -- the constructor's rows 0/1000/1001, the lock-on manager's -- gets exactly
//! the game's answer.

use std::sync::{
    OnceLock,
    atomic::{AtomicUsize, Ordering},
};

use eldenring::param::LOCK_CAM_PARAM_ST;
use er_game_base::game_build::{describe_build, is_supported_build};
use er_game_base::mem::{game_module_base, read_bytes};
use er_hook::UnionFn;

use super::served::{Served, ServedRow};
use super::{
    LOCK_CAM_FRAME_WINDOW_OFFSET, LOCK_CAM_OUT_ROW_SLOT, LOCK_CAM_OUT_STORE_WINDOW_OFFSET,
    LOCK_CAM_TABLE_WINDOW_OFFSET, LOOKUP_LOCK_CAM_PARAM_RVA,
};
use crate::log::possess_log;

// The windows, assembled by build.rs from named instructions and checked against the 2.7.2.0
// image (`eldenring-deobf-1.17.2.bin`).
include!(concat!(env!("OUT_DIR"), "/generated_getter_windows.rs"));

/// `(offset from the entry, window)` pairs that must all match before anything is hooked: the
/// frame setup, the table index handed to `GetParamResCap`, and the stores that fix the out
/// struct's layout.
const PINS: [(usize, &[u8]); 3] = [
    (LOCK_CAM_FRAME_WINDOW_OFFSET, LOCK_CAM_FRAME_WINDOW),
    (LOCK_CAM_TABLE_WINDOW_OFFSET, LOCK_CAM_TABLE_WINDOW),
    (LOCK_CAM_OUT_STORE_WINDOW_OFFSET, LOCK_CAM_OUT_STORE_WINDOW),
];

/// The copy being served, if any.
static SERVED: Served<LOCK_CAM_PARAM_ST> = Served::new();

/// The next handler or the game trampoline.
static ORIG_LOOKUP: AtomicUsize = AtomicUsize::new(0);

/// The install outcome: the hooked entry, or why there is none. Decided once, by the first
/// possession that wants an adapted camera, and never retried -- a refusal is a property of the
/// build, not of the moment.
static INSTALL: OnceLock<Result<usize, String>> = OnceLock::new();

/// Hook the getter if that has not been tried yet. `true` when the detour is live.
///
/// Lazy, like the overlay: a session that never possesses anything with `[camera] enabled` patches
/// nothing here.
pub(crate) fn ensure_installed() -> bool {
    INSTALL
        .get_or_init(|| {
            let outcome = install();
            match &outcome {
                Ok(entry) => possess_log(format_args!(
                    "camera: LookupLockCamParam at 0x{entry:x} hooked; while something is \
                     possessed it answers this mod's own LockCamParam row copy, and no resident \
                     param row is written"
                )),
                Err(why) => possess_log(format_args!(
                    "camera: {why}; not hooked, so possessions keep the vanilla camera framing"
                )),
            }
            outcome
        })
        .is_ok()
}

/// Serve `row` under `id` from now on. Only after [`ensure_installed`] said `true`.
pub(crate) fn publish(id: u32, row: LOCK_CAM_PARAM_ST) {
    SERVED.publish(id, row);
}

/// Stop serving. The copy stays allocated; see [`crate::camera::served`].
pub(crate) fn withdraw() {
    SERVED.withdraw();
}

/// Check the getter's bytes on the running build and register the detour on it.
fn install() -> Result<usize, String> {
    let rva = is_supported_build()
        .then_some(LOOKUP_LOCK_CAM_PARAM_RVA)
        .ok_or_else(|| {
            format!(
                "LookupLockCamParam: 2.7.2.0 rva 0x{LOOKUP_LOCK_CAM_PARAM_RVA:x} refused -- {}; \
                 only 2.7.2.0 is measured",
                describe_build()
            )
        })?;
    let base = game_module_base().map_err(|error| format!("LookupLockCamParam: {error:?}"))?;
    let entry = base + rva;
    for (offset, expected) in PINS {
        let mut bytes = vec![0u8; expected.len()];
        // SAFETY: a fault-tolerant read of code bytes.
        let read = unsafe { read_bytes(entry + offset, &mut bytes) };
        if !read || bytes != expected {
            return Err(format!(
                "LookupLockCamParam at 0x{entry:x}: +0x{offset:x} reads {bytes:02x?}, expected \
                 {expected:02x?}"
            ));
        }
    }
    // SAFETY: a runtime-derived entry whose bytes were just checked; the handler calls its slot
    // through `UnionFn`, and the getter takes two integer arguments and no float.
    unsafe {
        er_hook::register_union_hook_runtime_derived_following_arxan(
            entry,
            lookup_lock_cam_param,
            &ORIG_LOOKUP,
        )
    }
    .map_err(|status| format!("LookupLockCamParam at 0x{entry:x} refused: {status:?}"))?;
    Ok(entry)
}

/// `LookupLockCamParam` in the union's four-argument shape.
///
/// Runs on every `LockCamParam` lookup, from whichever thread asks, so the common path is the
/// game's own call plus one atomic load and one compare.
unsafe extern "system" fn lookup_lock_cam_param(
    out: usize,
    id: usize,
    c: usize,
    d: usize,
) -> usize {
    let next = ORIG_LOOKUP.load(Ordering::Acquire);
    if next == 0 {
        return 0;
    }
    // SAFETY: the slot holds the next handler or the trampoline, both with the `UnionFn` shape.
    let result = unsafe { core::mem::transmute::<usize, UnionFn>(next)(out, id, c, d) };
    if out != 0
        && let Some(served) = SERVED.answer(id)
    {
        serve(out, served);
    }
    result
}

/// Point `out`'s row slot at the served copy.
fn serve(out: usize, served: &'static ServedRow<LOCK_CAM_PARAM_ST>) {
    let slot = (out + LOCK_CAM_OUT_ROW_SLOT) as *mut usize;
    // SAFETY: `out` is the caller's lookup struct, whose row slot the getter has just written.
    unsafe { slot.write_unaligned(core::ptr::from_ref(&served.row) as usize) };
    if served.first_serve() {
        possess_log(format_args!(
            "camera: served this mod's LockCamParam copy for id {} for the first time",
            served.id
        ));
    }
}
