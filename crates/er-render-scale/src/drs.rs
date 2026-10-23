//! The detour on Elden Ring's per-frame dynamic resolution update, and the reads that report what
//! the game did with it. 2.7.2.0 only.
//!
//! # The chain (bd `er-dynamic-resolution-chain-static-2026-10-15`)
//!
//! The update (2.7.2.0 `0x141b300a0`) takes the controller's config in rcx: `*(GXRenderingSystem +
//! 0x13a70)`. It copies the current percentage out of `GxDrawContext+0xedc` into `+0x30`, and when
//! the enable byte (`+0x00`) is set it clamps that to `[+0x1c, +0x20]` before the setter writes the
//! percentage back; the view setup then draws the scene into a viewport that fraction of the
//! output, and the final composite upsamples it under a UI drawn at the output size. With the
//! load-balancer coupling (`+0x01`) set, a GX load balancer may override the clamp, so it is
//! cleared. So pinning min and max to one value, enable on and coupling off, holds the scene at
//! that scale. The update runs every frame, which makes it the clock for re-asserting the writes
//! after a graphics init or a render-mode change puts `LoadBalancerParam`'s row back.
//!
//! `GXRenderingSystem+0x13a78` gates the negative mip LOD bias the view setup applies at a reduced
//! scale (`-log2(1/scale)`), which shimmers at low sizes, so it is cleared while scaled.
//!
//! Measured live 2026-10-15 with the same writes from `scripts/frida/render-scale.js` (bd
//! `er-dynamic-resolution-runtime-proof-2026-10-15`): `1/24` at 3840x2160 drew a 160x92 viewport
//! and the game's GPU time went from 13.1-13.6 ms to 5.3-5.6 ms.
//!
//! # Native
//!
//! At divisor 1 the values the game had when this DLL first wrote are put back, once, and the DLL
//! stops writing, so a session at native is the game's own dynamic resolution untouched.
//!
//! # The signature
//!
//! `void(DrsConfig*, bool)`: rcx and dl only, no float or stack arguments (read out of
//! `eldenring-deobf-1.17.1.bin`), so the union's four-integer dispatcher forwards it exactly.

use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU32, AtomicU64, AtomicUsize, Ordering};

use er_game_base::game_build::{describe_build, is_supported_build};
use er_game_base::mem::{
    game_data_addr, game_module_base, read_bytes, safe_read_f32, safe_read_u8, safe_read_usize,
};
use er_game_base::rva::{GX_DRAW_CONTEXT_GLOBAL_RVA, GX_RENDERING_SYSTEM_GLOBAL_RVA};
use er_hook::UnionFn;

use crate::log;
use crate::report::{DrsFields, HookState, Originals, Readings};
use crate::scale::{self, NATIVE, Plan};

include!(concat!(env!("OUT_DIR"), "/generated_prologues.rs"));

/// The 2.7.2.0 rva of the update. It is used on 2.7.2.0 only, after its opening bytes are
/// compared with `DRS_UPDATE_PROLOGUE`.
const DRS_UPDATE_RVA: usize = 0x01b3_00a0;

/// `GXRenderingSystem+0x13a70`: the pointer to the controller's config.
const GXRS_DRS_CONFIG_OFFSET: usize = 0x13a70;
/// `GXRenderingSystem+0x13a78`: the mip LOD-bias gate.
const GXRS_LOD_BIAS_GATE_OFFSET: usize = 0x13a78;

/// The controller's config.
const DRS_ENABLE: usize = 0x00;
const DRS_COUPLE_LOAD_BALANCER: usize = 0x01;
const DRS_MIN: usize = 0x1c;
const DRS_MAX: usize = 0x20;
const DRS_CURRENT: usize = 0x30;

/// `GxDrawContext`: the output size beside the per-output scale, the size beside the primary
/// ratio, the viewport the view setup used as a ratio of the output, and the GPU timing block.
const CTX_OUTPUT_SIZE: usize = 0x3c8;
const CTX_SCENE_TARGET_SIZE: usize = 0xefc;
const CTX_VIEWPORT_RATIO: usize = 0xed0;
const CTX_GPU_TIMING: usize = 0xf30;
/// The GPU time in milliseconds, in the block `+0xf30` points at.
const GPU_MS: usize = 0xd344;

/// The divisor the settings ask for. The hook reads it every frame.
static SELECTED: AtomicU32 = AtomicU32::new(NATIVE);

/// Resolved addresses of the two globals, 0 until installed.
static GXRS_GLOBAL: AtomicUsize = AtomicUsize::new(0);
static CTX_GLOBAL: AtomicUsize = AtomicUsize::new(0);

static ORIG: AtomicUsize = AtomicUsize::new(0);
static INSTALLED: AtomicBool = AtomicBool::new(false);
static REFUSAL: Mutex<Option<String>> = Mutex::new(None);

static CALLS: AtomicU64 = AtomicU64::new(0);
static WRITES: AtomicU64 = AtomicU64::new(0);
static RESTORES: AtomicU64 = AtomicU64::new(0);
static FOREIGN: AtomicU64 = AtomicU64::new(0);

/// Our values are in place on the config at [`ORIG_FOR`].
static ACTIVE: AtomicBool = AtomicBool::new(false);
/// The divisor the last write put in force; 1 while native.
static APPLIED: AtomicU32 = AtomicU32::new(NATIVE);

/// The values the game had when this DLL first wrote, and which config they came from. Written and
/// read on the update's own thread; the atomics are for the telemetry's reads from the worker.
static ORIG_FOR: AtomicUsize = AtomicUsize::new(0);
static ORIG_ENABLE: AtomicU8 = AtomicU8::new(0);
static ORIG_COUPLE: AtomicU8 = AtomicU8::new(0);
static ORIG_MIN: AtomicU32 = AtomicU32::new(0);
static ORIG_MAX: AtomicU32 = AtomicU32::new(0);
static ORIG_LOD: AtomicU8 = AtomicU8::new(0);
static ORIG_CAPTURED: AtomicBool = AtomicBool::new(false);

/// Ask for `divisor`. The hook puts it in force on its next call when it is a clean divisor of the
/// screen, and native otherwise.
pub fn select(divisor: u32) {
    SELECTED.store(divisor.max(NATIVE), Ordering::Release);
}

/// The divisor asked for.
#[must_use]
pub fn selected() -> u32 {
    SELECTED.load(Ordering::Acquire)
}

/// The divisor in force.
#[must_use]
pub fn applied() -> u32 {
    APPLIED.load(Ordering::Acquire)
}

/// A pointer-sized read on the update's thread, of memory the update itself reads this call.
///
/// # Safety
///
/// `address` is a live global or field the game dereferences on this path.
unsafe fn read_ptr(address: usize) -> usize {
    // SAFETY: the caller's contract.
    unsafe { (address as *const usize).read_volatile() }
}

/// The output and scene target sizes out of a live draw context, on the update's thread.
///
/// # Safety
///
/// `ctx` is the live `GxDrawContext` the update reads this call.
unsafe fn sizes_at(ctx: usize) -> ([u32; 2], [u32; 2]) {
    // SAFETY: the caller's contract; two `u32` pairs inside the context.
    unsafe {
        let pair = |at: usize| {
            let p = (ctx + at) as *const u32;
            [p.read_volatile(), p.add(1).read_volatile()]
        };
        (pair(CTX_OUTPUT_SIZE), pair(CTX_SCENE_TARGET_SIZE))
    }
}

/// Put our values on `cfg`, first keeping what the game had there.
///
/// # Safety
///
/// `cfg` is the controller's live config and `gxrs` the live rendering system, on the update's
/// thread.
unsafe fn write(cfg: usize, gxrs: usize, divisor: u32, scale: f32) {
    let active_here = ACTIVE.load(Ordering::Acquire) && ORIG_FOR.load(Ordering::Acquire) == cfg;
    // SAFETY: the caller's contract; byte and float fields of the config, one byte of the system.
    unsafe {
        if !active_here {
            ORIG_ENABLE.store(*((cfg + DRS_ENABLE) as *const u8), Ordering::Relaxed);
            ORIG_COUPLE.store(
                *((cfg + DRS_COUPLE_LOAD_BALANCER) as *const u8),
                Ordering::Relaxed,
            );
            ORIG_MIN.store(*((cfg + DRS_MIN) as *const u32), Ordering::Relaxed);
            ORIG_MAX.store(*((cfg + DRS_MAX) as *const u32), Ordering::Relaxed);
            ORIG_LOD.store(
                *((gxrs + GXRS_LOD_BIAS_GATE_OFFSET) as *const u8),
                Ordering::Relaxed,
            );
            ORIG_FOR.store(cfg, Ordering::Release);
            ORIG_CAPTURED.store(true, Ordering::Release);
            ACTIVE.store(true, Ordering::Release);
        }
        *((cfg + DRS_ENABLE) as *mut u8) = 1;
        *((cfg + DRS_COUPLE_LOAD_BALANCER) as *mut u8) = 0;
        *((cfg + DRS_MIN) as *mut f32) = scale;
        *((cfg + DRS_MAX) as *mut f32) = scale;
        *((gxrs + GXRS_LOD_BIAS_GATE_OFFSET) as *mut u8) = 0;
    }
    APPLIED.store(divisor, Ordering::Release);
    WRITES.fetch_add(1, Ordering::Relaxed);
}

/// Put back what the game had, when it was taken from `cfg`; stop writing either way.
///
/// # Safety
///
/// As [`write`].
unsafe fn restore(cfg: usize, gxrs: usize) {
    if ORIG_FOR.load(Ordering::Acquire) == cfg {
        // SAFETY: the caller's contract.
        unsafe {
            *((cfg + DRS_ENABLE) as *mut u8) = ORIG_ENABLE.load(Ordering::Relaxed);
            *((cfg + DRS_COUPLE_LOAD_BALANCER) as *mut u8) = ORIG_COUPLE.load(Ordering::Relaxed);
            *((cfg + DRS_MIN) as *mut u32) = ORIG_MIN.load(Ordering::Relaxed);
            *((cfg + DRS_MAX) as *mut u32) = ORIG_MAX.load(Ordering::Relaxed);
            *((gxrs + GXRS_LOD_BIAS_GATE_OFFSET) as *mut u8) = ORIG_LOD.load(Ordering::Relaxed);
        }
    }
    ACTIVE.store(false, Ordering::Release);
    APPLIED.store(NATIVE, Ordering::Release);
    RESTORES.fetch_add(1, Ordering::Relaxed);
}

/// The update, in the union's four-argument shape (it takes two).
unsafe extern "system" fn drs_update_union(cfg: usize, flag: usize, c: usize, d: usize) -> usize {
    CALLS.fetch_add(1, Ordering::Relaxed);
    let gxrs_global = GXRS_GLOBAL.load(Ordering::Acquire);
    let ctx_global = CTX_GLOBAL.load(Ordering::Acquire);
    if gxrs_global != 0 && ctx_global != 0 && cfg != 0 {
        // SAFETY: both globals are the game's own, resolved for this build at install; the update
        // reads the draw context global itself on entry and the rendering system's on this path.
        let gxrs = unsafe { read_ptr(gxrs_global) };
        let config = if gxrs == 0 {
            0
        } else {
            // SAFETY: a field of the live rendering system.
            unsafe { read_ptr(gxrs + GXRS_DRS_CONFIG_OFFSET) }
        };
        if config != cfg {
            // The update is only ever handed the controller's config; count anything else
            // instead of writing through it.
            FOREIGN.fetch_add(1, Ordering::Relaxed);
        } else {
            // SAFETY: the draw context global the update dereferences on entry.
            let ctx = unsafe { read_ptr(ctx_global) };
            // SAFETY: a live context, read on the update's thread.
            let sizes = (ctx != 0).then(|| unsafe { sizes_at(ctx) });
            let active_here =
                ACTIVE.load(Ordering::Acquire) && ORIG_FOR.load(Ordering::Acquire) == cfg;
            match scale::plan(
                SELECTED.load(Ordering::Acquire),
                scale::offered_output(sizes),
                active_here,
            ) {
                // SAFETY: `cfg` is the controller's live config, checked above.
                Plan::Write { divisor, scale } => unsafe { write(cfg, gxrs, divisor, scale) },
                // SAFETY: as above.
                Plan::Restore => unsafe { restore(cfg, gxrs) },
                Plan::Leave => {
                    if ACTIVE.load(Ordering::Acquire) {
                        // Ours were on a config the game has since replaced; nothing to put back.
                        ACTIVE.store(false, Ordering::Release);
                        APPLIED.store(NATIVE, Ordering::Release);
                    }
                }
            }
        }
    }
    let next = ORIG.load(Ordering::Acquire);
    if next == 0 {
        return 0;
    }
    // SAFETY: the next handler or the trampoline, both `UnionFn`.
    let call: UnionFn = unsafe { core::mem::transmute::<usize, UnionFn>(next) };
    unsafe { call(cfg, flag, c, d) }
}

/// The opening bytes did not match on the last attempt, which can be the image not yet decrypted
/// this early; the worker tries again ([`retry`]).
static BYTES_PENDING: AtomicBool = AtomicBool::new(false);
/// Attempts made by [`retry`].
static RETRIES: AtomicU32 = AtomicU32::new(0);
/// How many seconds the worker keeps trying a byte mismatch before it is the answer.
const MAX_RETRIES: u32 = 60;

fn refuse(reason: String) -> Result<(), String> {
    let mut slot = REFUSAL
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if slot.as_deref() != Some(reason.as_str()) {
        log(format_args!("drs: not installed: {reason}"));
    }
    *slot = Some(reason.clone());
    Err(reason)
}

/// Once a second, on the worker: try the install again while its last refusal was a byte mismatch,
/// for [`MAX_RETRIES`] seconds.
pub fn retry() {
    if INSTALLED.load(Ordering::Acquire) || !BYTES_PENDING.load(Ordering::Acquire) {
        return;
    }
    if RETRIES.fetch_add(1, Ordering::Relaxed) >= MAX_RETRIES {
        BYTES_PENDING.store(false, Ordering::Release);
        log(format_args!(
            "drs: the update's bytes never matched in {MAX_RETRIES} s; giving up for this session"
        ));
        return;
    }
    let _ = install();
}

/// Detour the update on 2.7.2.0. Any other build, other bytes at the address, or a global that
/// does not resolve leaves the game's dynamic resolution alone and says why; the menu then shows
/// the reason and offers nothing.
pub fn install() -> Result<(), String> {
    if INSTALLED.load(Ordering::Acquire) {
        return Ok(());
    }
    // Measured on 2.7.2.0; the opening bytes are compared below before anything is hooked.
    let Some(rva) = is_supported_build().then_some(DRS_UPDATE_RVA) else {
        return refuse(format!(
            "the update was measured on 2.7.2.0 and this build is another: {}",
            describe_build()
        ));
    };
    let base = match game_module_base() {
        Ok(base) => base,
        Err(error) => return refuse(error),
    };
    // Both are 2.7.2.0 rvas; 0 on another build.
    let gxrs_global = game_data_addr(
        base,
        GX_RENDERING_SYSTEM_GLOBAL_RVA,
        "GX_RENDERING_SYSTEM_GLOBAL_RVA",
    );
    let ctx_global = game_data_addr(
        base,
        GX_DRAW_CONTEXT_GLOBAL_RVA,
        "GX_DRAW_CONTEXT_GLOBAL_RVA",
    );
    if gxrs_global == 0 || ctx_global == 0 {
        return refuse(format!(
            "a global was refused on this build (GXRenderingSystem 0x{gxrs_global:x}, \
             GxDrawContext 0x{ctx_global:x})"
        ));
    }
    GXRS_GLOBAL.store(gxrs_global, Ordering::Release);
    CTX_GLOBAL.store(ctx_global, Ordering::Release);
    let entry = base + rva;
    let mut bytes = [0u8; DRS_UPDATE_PROLOGUE.len()];
    // SAFETY: a fault-tolerant read of code bytes.
    let read = unsafe { read_bytes(entry, &mut bytes) };
    // Byte 0 may be Arxan's `jmp rel32`, which the installer follows; the rest must match under
    // the generated mask. On 2.7.2.0 the stack cookie's `mov rax,[rip+..]` reads `73 1d 13 02`
    // (the 1.17.1 build read `53 04 13 02`), and the pin carries the 2.7.2.0 encoding.
    let masked_eq = |from: usize| {
        (from..bytes.len()).all(|i| {
            bytes[i] & DRS_UPDATE_PROLOGUE_MASK[i]
                == DRS_UPDATE_PROLOGUE[i] & DRS_UPDATE_PROLOGUE_MASK[i]
        })
    };
    let matches = read && (masked_eq(0) || (bytes[0] == 0xe9 && masked_eq(5)));
    if !matches {
        BYTES_PENDING.store(true, Ordering::Release);
        return refuse(format!(
            "0x{entry:x} does not open like the 2.7.2.0 dynamic resolution update ({bytes:02x?})"
        ));
    }
    BYTES_PENDING.store(false, Ordering::Release);
    // SAFETY: a runtime-derived 2.7.2.0 entry whose bytes were just checked; the handler calls its
    // slot through `UnionFn`.
    match unsafe {
        er_hook::register_union_hook_runtime_derived_following_arxan(entry, drs_update_union, &ORIG)
    } {
        Ok(()) => {
            INSTALLED.store(true, Ordering::Release);
            *REFUSAL
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner) = None;
            log(format_args!(
                "drs: dynamic resolution update at 0x{entry:x} detoured; GXRenderingSystem global \
                 0x{gxrs_global:x}, GxDrawContext global 0x{ctx_global:x}"
            ));
            Ok(())
        }
        Err(status) => refuse(format!("the hook at 0x{entry:x} was refused: {status:?}")),
    }
}

/// The detour's state, for the menu and the telemetry.
#[must_use]
pub fn hook_state() -> HookState {
    HookState {
        installed: INSTALLED.load(Ordering::Acquire),
        refusal: REFUSAL
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone(),
        calls: CALLS.load(Ordering::Relaxed),
        writes: WRITES.load(Ordering::Relaxed),
        restores: RESTORES.load(Ordering::Relaxed),
        foreign_config: FOREIGN.load(Ordering::Relaxed),
        active: ACTIVE.load(Ordering::Acquire),
        applied: APPLIED.load(Ordering::Acquire),
        originals: Originals {
            captured: ORIG_CAPTURED.load(Ordering::Acquire),
            enable: ORIG_ENABLE.load(Ordering::Relaxed),
            couple: ORIG_COUPLE.load(Ordering::Relaxed),
            min: f32::from_bits(ORIG_MIN.load(Ordering::Relaxed)),
            max: f32::from_bits(ORIG_MAX.load(Ordering::Relaxed)),
            lod_bias_gate: ORIG_LOD.load(Ordering::Relaxed),
        },
    }
}

fn read_u32_pair(address: usize) -> Option<[u32; 2]> {
    let mut bytes = [0u8; 8];
    // SAFETY: a fault-tolerant read.
    if !unsafe { read_bytes(address, &mut bytes) } {
        return None;
    }
    let [a0, a1, a2, a3, b0, b1, b2, b3] = bytes;
    Some([
        u32::from_le_bytes([a0, a1, a2, a3]),
        u32::from_le_bytes([b0, b1, b2, b3]),
    ])
}

fn read_f32_pair(address: usize) -> Option<[f32; 2]> {
    // SAFETY: fault-tolerant reads.
    unsafe { Some([safe_read_f32(address)?, safe_read_f32(address + 4)?]) }
}

/// What the game holds now, read fault-tolerantly from any thread: the menu's every frame and the
/// worker's once a second.
#[must_use]
pub fn read() -> Readings {
    let ctx_global = CTX_GLOBAL.load(Ordering::Acquire);
    let gxrs_global = GXRS_GLOBAL.load(Ordering::Acquire);
    let mut readings = Readings::default();
    // SAFETY: fault-tolerant reads of a resolved global.
    let ctx = (ctx_global != 0)
        .then(|| unsafe { safe_read_usize(ctx_global) })
        .flatten()
        .filter(|&ctx| ctx != 0);
    if let Some(ctx) = ctx {
        readings.sizes =
            read_u32_pair(ctx + CTX_OUTPUT_SIZE).zip(read_u32_pair(ctx + CTX_SCENE_TARGET_SIZE));
        readings.viewport_ratio = read_f32_pair(ctx + CTX_VIEWPORT_RATIO);
        // SAFETY: fault-tolerant reads.
        readings.gpu_ms = unsafe { safe_read_usize(ctx + CTX_GPU_TIMING) }
            .filter(|&timing| timing != 0)
            .and_then(|timing| unsafe { safe_read_f32(timing + GPU_MS) });
    }
    // SAFETY: fault-tolerant reads of a resolved global and the fields behind it.
    let gxrs = (gxrs_global != 0)
        .then(|| unsafe { safe_read_usize(gxrs_global) })
        .flatten()
        .filter(|&gxrs| gxrs != 0);
    if let Some(gxrs) = gxrs {
        let config = unsafe { safe_read_usize(gxrs + GXRS_DRS_CONFIG_OFFSET) }.filter(|&c| c != 0);
        readings.drs = config.and_then(|cfg| unsafe {
            Some(DrsFields {
                enable: safe_read_u8(cfg + DRS_ENABLE)?,
                couple: safe_read_u8(cfg + DRS_COUPLE_LOAD_BALANCER)?,
                min: safe_read_f32(cfg + DRS_MIN)?,
                max: safe_read_f32(cfg + DRS_MAX)?,
                current: safe_read_f32(cfg + DRS_CURRENT)?,
                lod_bias_gate: safe_read_u8(gxrs + GXRS_LOD_BIAS_GATE_OFFSET)?,
            })
        });
    }
    readings
}
